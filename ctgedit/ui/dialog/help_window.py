"""The manual window: contents, search and page view."""

from __future__ import annotations
from ..style.theme import sp as _sp

import html
import re
import time
from typing import Dict, List, Optional

import shiboken6
from PySide6 import QtCore, QtGui, QtWidgets

from ... import help as H
from .. import search_rows as SR
from ..style.theme import COLORS

_CSS = """
body { color: %(text)s; font-size: 10.5pt; }
h1 { color: %(head)s; font-size: 19pt; font-weight: 600; margin-top: 4px; margin-bottom: 10px; }
h2 { color: %(head)s; font-size: 14pt; font-weight: 600; margin-top: 18px; margin-bottom: 6px; }
h3 { color: %(head)s; font-size: 11.5pt; font-weight: 600; margin-top: 14px; margin-bottom: 4px; }
h4 { color: %(dim)s; font-size: 10.5pt; font-weight: 600; margin-top: 10px; }
p { margin-top: 4px; margin-bottom: 8px; line-height: 135%%; }
li { margin-bottom: 4px; }
a { color: %(accent)s; text-decoration: none; }
code { font-family: Consolas, 'Courier New', monospace; color: #e6c07b; }
pre { font-family: Consolas, 'Courier New', monospace; background-color: %(panel)s; color: #e6c07b; padding: 8px; }
table.grid { border-collapse: collapse; margin-top: 6px; margin-bottom: 10px; }
table.grid th { background-color: %(raised)s; color: %(head)s; font-weight: 600; text-align: left; border: 1px solid %(border)s; }
table.grid td { border: 1px solid %(border)s; vertical-align: top; }
table.callout { margin-top: 6px; margin-bottom: 10px; }
table.note td { background-color: #25303c; border-left: 3px solid %(accent)s; }
table.tip td { background-color: #25352a; border-left: 3px solid #5fbf77; }
table.warning td, table.important td { background-color: #3a2a24; border-left: 3px solid #e0955c; }
table.gb td { background-color: #2c3036; border-left: 3px solid #b6bcc4; }
span.key { font-family: Consolas, monospace; background-color: %(raised)s; color: %(head)s; border: 1px solid %(border)s; }
hr { color: %(border)s; }
"""

#: the text zoom (Ctrl+= / Ctrl+-): its settings key, its steps and each step's factor
ZOOM_SETTING = "help/zoom"
SPLITTER_SETTING = "help/splitter"
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = -4, 10, 0.1
#: how long the heading F1 opened lights up (ms)
FLASH_MS = 1000
#: the menu commands the Search tab lists above the sections
MAX_COMMANDS = 3


def zoom_factor(zoom: int) -> float:
    return max(0.5, 1.0 + ZOOM_STEP * int(zoom))


def _stylesheet(zoom: int = 0) -> str:
    c = dict(COLORS)
    c.update(head="#f0f2f5", dim=COLORS["text_dim"], raised="#343941")
    # the fixed design colours (headings, code, callouts) in the active theme
    from ..style.theme import recolour, scale_fonts

    return recolour(scale_fonts(_CSS % c, zoom_factor(zoom)))


class HelpResultDelegate(SR.RowDelegate):
    """A Search hit: 'Page title > Section' (the words found marked) over two lines of its
    snippet with the words found in bold and the keys as key caps; a command row as the
    palette draws it."""

    LINES = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.words: List[str] = []

    def sizeHint(self, option, index) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        r = index.data(SR.ROW_ROLE)
        if r is None or r.kind != "help":
            return super().sizeHint(option, index)
        from ..style.theme import font, sp

        fm = QtGui.QFontMetrics(font("caption"))
        return QtCore.QSize(option.rect.width(), self.row_height() + fm.lineSpacing() * self.LINES + sp(1))

    def snippet_html(self, r) -> str:
        """The snippet as HTML: the section's keys as key caps, the words found in bold. One
        pass over the plain text, so a word never lands inside the markup ('style', 'color')
        and a key cap is never bolded inside."""
        from ..style.theme import COLORS

        plain = r.detail or ""
        keys = sorted({k for k in (r.meta or ()) if k}, key=len, reverse=True)
        words = sorted({w for w in self.words if len(w) >= 2}, key=len, reverse=True)
        alts = []
        if keys:
            alts.append(r"(?P<key>(?<!\w)(?:%s)(?!\w))" % "|".join(map(re.escape, keys)))
        if words:
            alts.append(r"(?P<word>(?i:(?<!\w)(?:%s)))" % "|".join(map(re.escape, words)))
        if not alts:
            return html.escape(plain, quote=False)
        cap = "<span style='background-color:%s; color:%s;'>&nbsp;%%s&nbsp;</span>" % (
            COLORS["raised"],
            COLORS["label"],
        )
        out = []
        pos = 0
        for m in re.finditer("|".join(alts), plain):
            out.append(html.escape(plain[pos : m.start()], quote=False))
            esc = html.escape(m.group(0), quote=False)
            out.append(cap % esc if m.lastgroup == "key" else "<b>%s</b>" % esc)
            pos = m.end()
        out.append(html.escape(plain[pos:], quote=False))
        return "".join(out)

    @staticmethod
    def snippet_x(left: int) -> int:
        """Where the snippet starts: under the title, past the icon (RowDelegate draws it
        px(16) wide at every text size)."""
        from ..style.theme import px, sp

        return left + sp(2) + px(16) + sp(2)

    def paint(self, p: QtGui.QPainter, option, index) -> None:
        r = index.data(SR.ROW_ROLE)
        if r is None or r.kind != "help":
            return super().paint(p, option, index)
        from ..style.theme import font, sp

        detail = r.detail
        r.detail = ""
        try:
            super().paint(p, option, index)
        finally:
            r.detail = detail
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        rect = option.rect
        x = self.snippet_x(rect.left())
        top = rect.top() + self.row_height() - sp(1)
        w = rect.right() - sp(2) - x
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(font("caption"))
        doc.setDocumentMargin(0)
        col = COLORS["sel_text_dim" if selected else "text_dim"]
        doc.setHtml("<div style='color:%s;'>%s</div>" % (col, self.snippet_html(r)))
        doc.setTextWidth(max(10, w))
        fm = QtGui.QFontMetrics(font("caption"))
        p.save()
        p.translate(x, top)
        clip = QtCore.QRectF(0, 0, w, fm.lineSpacing() * self.LINES + 1)
        p.setClipRect(clip)
        ctx = QtGui.QAbstractTextDocumentLayout.PaintContext()
        ctx.clip = clip
        ctx.palette.setColor(QtGui.QPalette.Text, QtGui.QColor(col))
        doc.documentLayout().draw(p, ctx)
        p.restore()


class HelpWindow(QtWidgets.QMainWindow):
    """The manual. ``actionRequested(name)`` asks the editor to run a
    command (``action:<name>`` links)."""

    actionRequested = QtCore.Signal(str)
    _inst: Optional["HelpWindow"] = None

    @classmethod
    def instance(cls, parent=None) -> "HelpWindow":
        # (a window something deleted is made again)
        if cls._inst is None or not shiboken6.isValid(cls._inst):
            cls._inst = HelpWindow(parent)
        return cls._inst

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Window)
        self.setWindowTitle("ctgedit Help")
        self.setObjectName("HelpWindow")
        self.resize(1080, 760)
        self._history: List[tuple] = []
        self._pos = -1
        self._current = ("", "")
        self._items: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._show_targets: list = []
        self._restored = False
        self.flashed_anchor = ""
        from ..prefs.settings import app_settings

        try:
            self._zoom = int(app_settings().value(ZOOM_SETTING, 0))
        except TypeError, ValueError:  # swallow-ok: a garbled setting reads as 0
            self._zoom = 0
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom))

        from ..style.theme import icon

        tb = self.addToolBar("Navigation")
        tb.setMovable(False)
        tb.setObjectName("HelpNav")
        self.act_back = tb.addAction(icon("nav_back"), "Back", self.back)
        self.act_back.setShortcut(QtGui.QKeySequence.Back)
        self.act_back.setToolTip("Back (Alt+Left)")
        self.act_fwd = tb.addAction(icon("nav_forward"), "Forward", self.forward)
        self.act_fwd.setShortcut(QtGui.QKeySequence.Forward)
        self.act_fwd.setToolTip("Forward (Alt+Right)")
        self.act_home = tb.addAction(icon("home"), "Contents", lambda: self.show_topic("index"))
        self.act_home.setShortcut(QtGui.QKeySequence("Alt+Home"))
        self.act_home.setToolTip("Contents (Alt+Home)")
        tb.addSeparator()
        self.find_edit = QtWidgets.QLineEdit()
        self.find_edit.setPlaceholderText("Find in page (Ctrl+F)")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.setMaximumWidth(260)
        self.find_edit.returnPressed.connect(self._find_next)
        # the '.*' toggle every search box has (theme.RegexToggle)
        from ..style.theme import RegexToggle

        self.find_regex = RegexToggle(self.find_edit, "Find in page")
        tb.addWidget(self.find_edit)
        # (a miss says so here; the page stays where the reader was)
        from ..style.theme import note_label

        self.find_note = note_label("", "text_dim")
        self.find_note.setContentsMargins(_sp(1), 0, _sp(1), 0)
        tb.addWidget(self.find_note)
        self.find_edit.textChanged.connect(lambda _t: self.find_note.setText(""))
        QtGui.QShortcut(QtGui.QKeySequence.Find, self, self.find_edit.setFocus)
        tb.addSeparator()
        self.act_zoom_out = tb.addAction(icon("zoom_out"), "Smaller Text", lambda: self.zoom_by(-1))
        self.act_zoom_out.setShortcuts([QtGui.QKeySequence("Ctrl+-")])
        self.act_zoom_out.setToolTip("Smaller text (Ctrl+-)")
        self.act_zoom_in = tb.addAction(icon("zoom_in"), "Larger Text", lambda: self.zoom_by(1))
        self.act_zoom_in.setShortcuts([QtGui.QKeySequence("Ctrl+="), QtGui.QKeySequence("Ctrl++")])
        self.act_zoom_in.setToolTip("Larger text (Ctrl+=)")
        self.act_zoom_reset = QtGui.QAction("Text Size 100 %", self)
        self.act_zoom_reset.triggered.connect(lambda: self.set_zoom(0))
        self.addAction(self.act_zoom_reset)
        self.apply_key_settings()
        self.act_print = tb.addAction(icon("print"), "Print…", self._print)
        self.act_print.setShortcut(QtGui.QKeySequence.Print)
        self.act_print.setToolTip("Print this page (Ctrl+P)")
        for a in (
            self.act_back,
            self.act_fwd,
            self.act_home,
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_zoom_reset,
            self.act_print,
        ):
            a.setShortcutContext(QtCore.Qt.WindowShortcut)

        # left: Contents | Search
        self.tabs = QtWidgets.QTabWidget()
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        from ..tables import tip_cut_cells

        tip_cut_cells(self.tree)  # (a cut title shows whole when pointed at)
        self.tree.itemActivated.connect(self._tree_open)
        self.tree.itemClicked.connect(self._tree_open)
        self.tabs.addTab(self.tree, "Contents")
        sw = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(sw)
        sl.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        from ..style.theme import SearchField

        self.search_edit = SearchField("Search the manual", delay_ms=180, regex_key="Search the manual")
        self.results = SR.RowList(sw, HelpResultDelegate())
        self.results.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.results.setAccessibleName("Search results")
        # a click opens a row (a command row shows its menu item; Shift runs it, as the
        # manual's own command links), so does Enter in the list (eventFilter). Not
        # 'activated': a double-click sends clicked and then activated
        self.results.clicked.connect(self._result_activated)
        self.results.installEventFilter(self)
        self._last_run = ("", 0.0)
        self._front_timer = QtCore.QTimer(self)
        self._front_timer.setSingleShot(True)
        self._front_timer.timeout.connect(self._editor_to_front)
        sl.addWidget(self.search_edit)
        sl.addWidget(self.results)
        self.search_edit.set_list_view(self.results)
        self.search_edit.returnPressed.connect(self._result_enter)
        self.tabs.addTab(sw, "Search")
        self.search_edit.searchChanged.connect(lambda _t: self._run_search())
        self.search_regex = self.search_edit.regex_toggle

        self.view = QtWidgets.QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        self.view.highlighted.connect(
            lambda url: self.statusBar().showMessage(
                self._describe(url.toString()) if url.toString() else "", 4000
            )
        )
        self.view.document().setDefaultStyleSheet(_stylesheet(self._zoom))
        self.view.document().setDocumentMargin(22)
        # (written in the design colours, which the theme maps to the active scheme and
        # accent on every change; an edge in the background colour: the keyboard focus
        # ring takes its place)
        self.view.setStyleSheet(
            "QTextBrowser { background: #1f2226; border: 1px solid #1f2226; }"
            'QTextBrowser[kbdFocus="true"] { border: 1px solid #4da3ff; }'
        )

        self.split = QtWidgets.QSplitter()
        self.split.setObjectName("HelpSplitter")
        self.split.addWidget(self.tabs)
        self.split.addWidget(self.view)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([290, 790])
        self.setCentralWidget(self.split)
        # (its messages stay as long as Settings > Appearance > Messages stay says)
        from ..notify import ScaledStatusBar

        self.setStatusBar(ScaledStatusBar(self))
        self._flash_timer = QtCore.QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        self._fill_tree()
        self._update_nav()

    # ------------------------------------------------------------ layout memory
    def showEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if not self._restored:
            self._restored = True
            from .. import help as UH

            UH.restore_window_geometry(self)
            from ..prefs.settings import app_settings

            state = app_settings().value(SPLITTER_SETTING, None)
            if isinstance(state, (QtCore.QByteArray, bytes)) and state:
                self.split.restoreState(QtCore.QByteArray(state))
        super().showEvent(e)

    def save_layout(self) -> None:
        """The window's size and place (geometry/HelpWindow) and the contents pane's
        width (help/splitter)."""
        from .. import help as UH
        from ..prefs.settings import app_settings

        UH.save_window_geometry(self)
        try:
            app_settings().setValue(SPLITTER_SETTING, self.split.saveState())
        except RuntimeError:  # swallow-ok: the window is being deleted
            pass

    def hideEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if not e.spontaneous() and self._restored:
            self.save_layout()
        super().hideEvent(e)

    def closeEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if self._restored:
            self.save_layout()
        super().closeEvent(e)

    # ------------------------------------------------------------ text zoom
    @property
    def zoom(self) -> int:
        return self._zoom

    def zoom_by(self, step: int) -> None:
        self.set_zoom(self._zoom + step)

    def apply_key_settings(self) -> None:
        """Settings > Keyboard > "Text size 100 % in the manual": Ctrl+0 (as in a browser)
        or Ctrl+1 (as View > Zoom 100%)."""
        from ..editor.view_consistency import help_reset_key

        key = help_reset_key()
        self.act_zoom_reset.setShortcut(QtGui.QKeySequence(key))
        self.act_zoom_reset.setToolTip("Text size 100 %% (%s)" % key)

    def set_zoom(self, zoom: int) -> None:
        """The text size: 0 = as designed, each step 10 % (help/zoom)."""
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, int(zoom)))
        if zoom == self._zoom:
            return
        self._zoom = zoom
        from ..prefs.settings import app_settings

        app_settings().setValue(ZOOM_SETTING, zoom)
        self._rerender()
        self.statusBar().showMessage("Text size %d %%." % round(zoom_factor(zoom) * 100), 3000)

    def _rerender(self) -> None:
        self.view.document().setDefaultStyleSheet(_stylesheet(self._zoom))
        if self._current[0]:
            bar = self.view.verticalScrollBar()
            frac = bar.value() / max(1, bar.maximum())
            self.view.setHtml("<html><body>%s</body></html>" % self._page_body(self._current[0]))
            bar.setValue(int(round(frac * bar.maximum())))

    # ------------------------------------------------------------ contents
    def _fill_tree(self) -> None:
        self.tree.clear()
        self._items.clear()

        def add(entries, parent):
            for e in entries:
                it = QtWidgets.QTreeWidgetItem([e.title])
                it.setData(0, QtCore.Qt.UserRole, e.id)
                if parent is None:
                    self.tree.addTopLevelItem(it)
                else:
                    parent.addChild(it)
                self._items.setdefault(e.id, it)
                add(e.children, it)

        try:
            add(H.toc(), None)
        except OSError, ValueError:
            pass
        self.tree.expandToDepth(0)

    def _tree_open(self, item, _col=0) -> None:
        t = item.data(0, QtCore.Qt.UserRole)
        if t and t != self._current[0]:
            self.show_topic(t)

    # -------------------------------------------------------------- search
    def _query_words(self) -> List[str]:
        return [w for w in re.findall(r"\w+", self.search_edit.text().lower()) if w]

    def _command_rows(self, q: str) -> list:
        """Up to three menu commands that match (as the palette matches them)."""
        from .. import help as UH
        from .palette import Command, command_rank, command_row, harvest_actions

        main = UH.action_target()
        if main is None or len(q) < 2:
            return []
        scored = []
        try:
            for k, (a, crumb, label) in enumerate(harvest_actions(main.menuBar())):
                c = Command(a, crumb, label, k)
                s = command_rank(q, c.bare, "")
                if 0 <= s < 100:
                    scored.append((s, k, c))
        except RuntimeError:  # swallow-ok: the editor window went
            return []
        scored.sort(key=lambda t: (t[0], t[1]))
        out = []
        for _s, _k, c in scored[:MAX_COMMANDS]:
            r = command_row(main, c)
            r.text = "Command: %s > %s" % (c.crumb, c.name) if c.crumb else "Command: %s" % c.name
            r.detail = "Shows it in the menu. Shift+click runs it"
            m = SR.match(q, r.text, substring_min=3)
            r.hits = tuple(m[1]) if m else ()
            out.append(r)
        return out

    def _run_search(self) -> None:
        q = self.search_edit.text().strip()
        dl = self.results.itemDelegate()
        dl.words = self._query_words()
        if not q:
            self.results.set_rows([])
            return
        m = self.search_regex.matcher(q)
        rows = [] if self.search_regex.on else self._command_rows(q)
        hits = H.search_sections(q, regex=self.search_regex.on)
        from ..style.theme import icon

        for h in hits:
            title = H.page_title(h.topic)
            text = title if not h.anchor else "%s > %s" % (title, h.heading)
            mm = SR.match(q, text, substring_min=3)
            sec = H.section_of(h.topic, h.anchor)
            rows.append(
                SR.Row(
                    "help",
                    text,
                    h.snippet,
                    h,
                    hits=mm[1] if mm else (),
                    icon=icon("page"),
                    tip=h.snippet,
                    meta=sec.keys if sec is not None else (),
                )
            )
        if not hits and not rows:
            rows = [
                SR.note("Not a regular expression: %s" % m.error if m.error else "No pages match '%s'." % q)
            ]
        # (the first section is the highlighted row, not a command above it: Enter in the
        # box opens that section)
        first = next((k for k, r in enumerate(rows) if r.kind == "help"), -2)
        self.results.set_rows(rows, current=first)

    def result_rows(self) -> list:
        return self.results.rows()

    def _result_enter(self) -> None:
        """Enter in the search box: the highlighted section opens (the first one found,
        unless the arrows moved). It never runs a command: on a command row it shows the
        command in the editor's menu."""
        self.search_edit.flush()  # (the rows of what was typed, not of the pause before)
        r = self.results.current_row_obj()
        if r is None or not r.selectable:
            return
        if r.kind == "command":
            from .. import help as UH

            main = UH.action_target()
            if main is not None:
                UH.show_command(main, r.data[0], run=False)
            return
        self.open_result(r)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        if (
            obj is self.results
            and e.type() == QtCore.QEvent.KeyPress
            and e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
            and not e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)
        ):
            self._result_activated(self.results.currentIndex())
            return True
        return super().eventFilter(obj, e)

    def _result_activated(self, index) -> None:
        r = index.data(SR.ROW_ROLE) if index.isValid() else None
        if r is None or not r.selectable:
            return
        self.open_result(r)

    def open_result(self, r, run: Optional[bool] = None) -> None:
        """A Search row: a command is shown in the editor's menu (``run`` or Shift held: it
        runs; looking up Delete never deletes); a section opens at its heading with the
        first word found selected."""
        if r.kind == "command":
            if run is None:
                run = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier)
            # (a second click of the same row the same way within a double-click's time does
            # not do it again)
            now = time.monotonic()
            last, when = self._last_run
            key = (r.text, run)
            if last == key and ((now - when) * 1000 < QtWidgets.QApplication.doubleClickInterval()):
                return
            self._last_run = (key, now)
            action = r.data[0]
            from .. import help as UH

            main = UH.action_target()
            if main is not None and not run:
                # (once a double-click could be over: its second click would close the menu)
                QtCore.QTimer.singleShot(
                    QtWidgets.QApplication.doubleClickInterval(),
                    main,
                    lambda m=main, a=action: UH.show_command(m, a, run=False),
                )
                return
            if main is not None:
                UH.show_command(main, action, run=True, front=False)
                # (the editor comes to the front once a double-click could be over: its
                # second click lands on this list, not on the page behind)
                self._front_timer.start(QtWidgets.QApplication.doubleClickInterval())
            return
        h = r.data
        if (h.topic, h.anchor) != self._current:
            # (a click and the double-click after it open the section once)
            self.show_topic(h.topic, h.anchor)
        else:
            self.view.scrollToAnchor(h.anchor)
        self.select_in_section(h.anchor)

    def select_in_section(self, anchor: str) -> bool:
        """Select the first word of the search found from the heading ``anchor`` on (the
        page's top for '')."""
        doc = self.view.document()
        start = self.anchor_position(anchor) if anchor else 0
        if start < 0:
            start = 0
        cur = QtGui.QTextCursor(doc)
        cur.setPosition(start)
        found = None
        if self.search_regex.on:
            q = self.search_edit.text().strip()
            if q and self.search_regex.matcher(q).ok:
                found = doc.find(
                    QtCore.QRegularExpression(q, QtCore.QRegularExpression.CaseInsensitiveOption), cur
                )
        else:
            words = self.search_edit.text().split()
            phrase = " ".join(words)
            for what in ([phrase] if len(words) > 1 else []) + words[:1]:
                f = doc.find(what, cur)
                if not f.isNull():
                    found = f
                    break
        if found is None or found.isNull():
            return False
        self.view.setTextCursor(found)
        self.view.ensureCursorVisible()
        return True

    def _editor_to_front(self) -> None:
        from .. import help as UH

        main = UH.action_target()
        if main is None:
            return
        try:
            main.raise_()
            main.activateWindow()
        except RuntimeError:  # swallow-ok: the editor window went
            pass

    def search_for(self, text: str) -> None:
        self.tabs.setCurrentIndex(1)
        self.search_edit.setText(text)
        self.search_edit.cancel()
        self._run_search()

    def _theme_changed(self) -> None:
        """Settings > Appearance changed: the page in the new colours."""
        self.view.document().setDefaultStyleSheet(_stylesheet(self._zoom))
        if self._current[0]:
            pos = self.view.verticalScrollBar().value()
            self.view.setHtml("<html><body>%s</body></html>" % self._page_body(self._current[0]))
            self.view.verticalScrollBar().setValue(pos)
        self.results.viewport().update()

    # ---------------------------------------------------------------- view
    def _page_body(self, topic: str) -> str:
        """The page HTML with its menu paths as 'show:' links (ui/help.link_menu_paths)."""
        body = H.page_html(topic)
        if H.is_generated(topic):
            self._show_targets = []
            return body
        from .. import help as UH

        try:
            body, self._show_targets = UH.link_menu_paths(body, UH.action_target())
        except Exception:  # noqa: BLE001 -- the page shows without the links
            from ...infra import logsetup

            logsetup.note_exception("help_window.page_body")
            self._show_targets = []
        return body

    def show_topic(self, topic: str, anchor: str = "", record: bool = True) -> None:
        if not topic:
            topic = "index"
        if not H.exists(topic):
            anchor = ""
        body = self._page_body(topic)
        self.view.setHtml("<html><body>%s</body></html>" % body)
        if anchor:
            self.view.scrollToAnchor(anchor)
        else:
            self.view.verticalScrollBar().setValue(0)
        self._current = (topic, anchor)
        if record:
            del self._history[self._pos + 1 :]
            self._history.append((topic, anchor))
            self._pos = len(self._history) - 1
        it = self._items.get(topic)
        if it is not None:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)
            self.tree.blockSignals(False)
        self.setWindowTitle("%s - ctgedit Help" % H.page_title(topic))
        self._update_nav()

    def anchor_position(self, anchor: str) -> int:
        """Where heading ``anchor`` starts in the page shown (-1 = not there)."""
        doc = self.view.document()
        b = doc.begin()
        while b.isValid():
            # (an empty '<a name>' before a heading names the heading's block)
            if anchor in b.charFormat().anchorNames():
                return b.position()
            it = b.begin()
            while not it.atEnd():
                f = it.fragment()
                if f.isValid() and anchor in f.charFormat().anchorNames():
                    return f.position()
                it += 1
            b = b.next()
        return -1

    def flash_anchor(self, anchor: str) -> bool:
        """Light up heading ``anchor`` (note_bg) for a second: where F1 landed."""
        pos = self.anchor_position(anchor)
        if pos < 0:
            return False
        doc = self.view.document()
        block = doc.findBlock(pos)
        if not block.isValid():
            return False
        sel = QtWidgets.QTextEdit.ExtraSelection()
        sel.cursor = QtGui.QTextCursor(block)
        sel.cursor.movePosition(QtGui.QTextCursor.EndOfBlock, QtGui.QTextCursor.KeepAnchor)
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QColor(COLORS["note_bg"]))
        fmt.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
        sel.format = fmt
        self.view.setExtraSelections([sel])
        self.flashed_anchor = anchor
        self._flash_timer.start(FLASH_MS)
        return True

    def _end_flash(self) -> None:
        self.view.setExtraSelections([])
        self.flashed_anchor = ""

    def current_topic(self) -> str:
        return self._current[0]

    def current_anchor(self) -> str:
        """The heading anchor the page was opened on ('' = its top)."""
        return self._current[1]

    def back(self) -> None:
        if self._pos > 0:
            self._pos -= 1
            self.show_topic(*self._history[self._pos], record=False)

    def forward(self) -> None:
        if self._pos + 1 < len(self._history):
            self._pos += 1
            self.show_topic(*self._history[self._pos], record=False)

    def _update_nav(self) -> None:
        self.act_back.setEnabled(self._pos > 0)
        self.act_fwd.setEnabled(self._pos + 1 < len(self._history))

    def show_target(self, target: str):
        """The target of a 'show:<n>' link of the page shown (None = not one)."""
        if not target.startswith("show:"):
            return None
        try:
            return self._show_targets[int(target[len("show:") :])]
        except ValueError, IndexError:
            return None

    def _describe(self, target: str) -> str:
        if target.startswith("action:"):
            return "Runs the command in the editor"
        if target.startswith("show:"):
            t = self.show_target(target)
            if t is None:
                return ""
            if t[0] == "settings":
                return "Opens Settings at %s" % t[1]
            return "Shows %s in the editor. Shift+click runs it" % t[2]
        if target.startswith("aveva:"):
            return "Opens the AVEVA Plant SCADA reference"
        if target.startswith(("http:", "https:", "file:")):
            return target
        page, _a = H.split_link(target)
        return "Help: %s" % H.page_title(page) if page else ""

    def _link(self, url: QtCore.QUrl) -> None:
        target = url.toString()
        if target.startswith("action:"):
            self.actionRequested.emit(target[len("action:") :])
            return
        if target.startswith("show:"):
            t = self.show_target(target)
            if t is not None:
                from .. import help as UH

                shift = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier)
                UH.follow_show(t, shift=shift)
            return
        if target.startswith("aveva:"):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(H.aveva_target(target[len("aveva:") :])))
            return
        if url.scheme() in ("http", "https", "file", "mailto"):
            QtGui.QDesktopServices.openUrl(url)
            return
        page, anchor = H.split_link(target)
        if not page:  # '#anchor' on this page
            self.view.scrollToAnchor(anchor)
            return
        self.show_topic(page, anchor)

    def _find_next(self) -> None:
        text = self.find_edit.text()
        if not text:
            return
        what = text
        if self.find_regex.on:
            if not self.find_regex.matcher(text).ok:
                return  # (the box says why)
            what = QtCore.QRegularExpression(text, QtCore.QRegularExpression.CaseInsensitiveOption)
        # forward from the cursor, then once from the top, on a cursor of its own: the view
        # moves only to a match (a miss keeps the reader's place)
        doc = self.view.document()
        found = doc.find(what, self.view.textCursor())
        if found.isNull():
            found = doc.find(what, QtGui.QTextCursor(doc))
        if found.isNull():
            self.find_note.setText("No matches on this page")
            return
        self.find_note.setText("")
        self.view.setTextCursor(found)
        self.view.ensureCursorVisible()

    def _print(self) -> None:
        from PySide6 import QtPrintSupport

        printer = QtPrintSupport.QPrinter()
        dlg = QtPrintSupport.QPrintDialog(printer, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            doc = QtGui.QTextDocument()
            doc.setDefaultStyleSheet(
                _stylesheet().replace(COLORS["text"], "#000000").replace("#f0f2f5", "#000000")
            )
            doc.setHtml("<html><body>%s</body></html>" % H.page_html(self._current[0]))
            doc.print_(printer)

    def reload(self) -> None:
        """Re-read the pages (after they changed on disk)."""
        H._toc_raw.cache_clear()
        self._fill_tree()
        if self._current[0]:
            self.show_topic(*self._current, record=False)


#: keys that act on the canvas (or anywhere) without a menu entry of their
#: own: (keys, what they do). Kept next to the generated menu tables; the
#: canvas / main window code that handles them is named in each comment.
CANVAS_KEYS = [
    # MainWindow._install_canvas_keys / CanvasView.keyPressEvent
    (
        "Arrow keys, Home, End, Page Up, Page Down",
        ("Move the selection, or the selected nodes, one step (Home, Page Up, End, Page Down: diagonally)"),
    ),
    (
        "Shift + arrow keys",
        "Resize the selection from its top-left corner",
    ),
    ("Ctrl+Shift + arrow keys", "Resize the selection keeping its proportions"),
    ("Insert, + (plus)", "Add a node where the pointer is on the selected line, pipe or polygon"),
    (
        "Esc while dragging",
        "Cancel the move, resize, selection box or zoom box",
    ),
    (
        "Esc",
        (
            "Cancel the shape being drawn, then go back to the Select tool, then clear the node "
            "selection, then the selection"
        ),
    ),
    ("Enter, double-click, right-click", "Finish a polygon or pipe"),
    ("Backspace (drawing)", "Take the last point of a polygon or pipe back"),
    (
        "Hold Space and drag",
        "Pan the page (with the pointer over the canvas)",
    ),
    ("Middle mouse button drag", "Pan the page"),
    (
        "Middle mouse button double-click",
        "Fit All Objects",
    ),
    ("Mouse wheel", "Zoom in or out about the pointer (it stops at 100 % for one notch)"),
    ("Ctrl + mouse wheel, Alt + mouse wheel", "Zoom; with Alt in small steps"),
    ("Shift + mouse wheel, sideways wheel", "Scroll sideways"),
    (
        "Touchpad: two fingers, pinch",
        (
            "Two fingers scroll like the mouse wheel, or pan with the touchpad setting on "
            "(Settings > Canvas, off by default). With it on, small scroll steps count as a "
            "touchpad: Remote Desktop and a smooth-scrolling mouse send them too. A pinch zooms"
        ),
    ),
    (
        "Mouse back / forward buttons",
        "Previous View, Next View",
    ),
    ("Hold Z", "Zoom box for a moment: drag a box to zoom into it, let go of Z for the tool you had"),
    (
        "Ctrl+Shift + drag (Select or Hand tool)",
        "Zoom into the box you drag",
    ),
    ("Zoom box: click, Alt + click", "Zoom in or out 2 times about the point"),
    ("Drag from a ruler", "Add a guideline where you let go"),
    (
        "Ctrl while drawing",
        "Keep a line or segment horizontal / vertical; draw a square or "
        "circle (Shift with the design tools keys)",
    ),
    ("Shift while drawing", "Draw a box or ellipse from its centre (Alt with the design tools keys)"),
    ("Ctrl + drag a handle", "Resize keeping the proportions (Shift with the design tools keys)"),
    ("Shift + drag a handle", "Resize about the centre (Alt with the design tools keys)"),
    ("Alt + click", "Select the next object under the pointer (click again to go deeper)"),
    (
        "Alt while dragging",
        "Move without snapping to the grid, guidelines or smart guides (Alt+Shift when Alt copies)",
    ),
    ("Shift while dragging", "Move horizontally or vertically only (Ctrl when Alt copies)"),
    ("Ctrl when releasing a drag", "Leave the objects and drop a copy (Alt by Settings)"),
    ("Shift + drag, Ctrl + drag a box", "Add to the selection, or add and take out"),
    ("Drag the round grip", "Turn the selection a quarter or half turn"),
    # CanvasView keyboard selection (Tab, Ctrl+Alt + arrows, Enter) and its menu key
    ("Tab, Shift+Tab", "Select the next or the previous object"),
    ("Ctrl+Alt + arrow keys", "Select the nearest object in that direction"),
    ("Enter", "Open the selected object's properties or genie parameters"),
    ("Shift+Enter, Ctrl+Enter", "Select the group the object is in, or its first object"),
    ("Shift+F10, Menu key", "Open the right-click menu of the selection"),
    ("Arrow keys, Home, End with nothing selected", "Scroll the page"),
    (
        "Ctrl + double-click",
        "Open the properties of the exact object under the pointer, even inside a group or genie",
    ),
    ("Ctrl when releasing a dragged guideline", "Leave the guideline and add a copy"),
    # (the command palette's keys are commands of the View menu: Command Palette,
    # Command Palette at the Pointer; Settings > Keyboard changes them)
    # the in-place text editor (ui/editor/inplace_text.py)
    (
        "Enter (editing text)",
        "New line",
    ),
    ("Ctrl+Enter, Tab, click elsewhere (editing text)", "Store the text"),
    ("Esc (editing text)", "Cancel the edit"),
    # ui/help.py
    (
        "F1",
        "Help on what has the focus: the dialog and its tab, the menu item or command "
        "button under the pointer, the active tool or the selection",
    ),
]


def shortcut_reference_html(menubar: QtWidgets.QMenuBar) -> str:
    """The keyboard-shortcut reference, built from the editor's live menus
    (every visible command with a key, per menu) + the canvas keys."""
    from .palette import harvest_actions

    rows: List[str] = [
        "<h1>Keyboard shortcuts</h1>",
        "<p>Every command that has a keyboard shortcut, grouped by menu. "
        "Keys that work on the canvas without a menu command are listed at "
        "the end.</p>",
        "<p><a href='action:shortcuts_window'>Open the Keyboard Shortcuts "
        "window</a> (Help &gt; Keyboard Shortcuts) to search them by name or "
        "by key, change them or print a one-page list.</p>",
    ]

    def label(text: str) -> str:
        return text.replace("&&", "\x00").replace("&", "").replace("\x00", "&").split("\t")[0]

    groups: Dict[str, list] = {}
    order: List[str] = []
    # harvest_actions walks the menus without QAction.menu() (which would
    # invalidate the dynamic menus' Python wrappers)
    from ..prefs.keymap import native, shown_keys

    for a, crumb, _text in harvest_actions(menubar):
        try:
            # (with the keys the canvas runs it with: Delete's minus keys)
            keys = [native(k) for k in shown_keys(a)]
            text = a.text()
        except RuntimeError:
            continue
        if not keys and "\t" in text:
            keys = [text.split("\t", 1)[1]]
        if not keys:
            continue
        parts = crumb.split(" > ")
        top = label(parts[0])
        if top not in groups:
            groups[top] = []
            order.append(top)
        where = "".join(label(p) + " › " for p in parts[1:])
        groups[top].append((where, label(text), ", ".join(keys)))
    head = (
        '<table class="grid" cellspacing="0" cellpadding="5" width="100%">'
        '<tr><th>Command</th><th width="30%">Keys</th></tr>'
    )
    for top in order:
        rows.append("<h2>%s</h2>" % html.escape(top))
        rows.append(head)
        for where, name, keys in groups[top]:
            rows.append(
                "<tr><td>%s%s</td><td><span class='key'>%s</span></td></tr>"
                % (html.escape(where), html.escape(name), html.escape(keys))
            )
        rows.append("</table>")
    rows.append("<h2>On the canvas</h2>")
    rows.append(
        '<table class="grid" cellspacing="0" cellpadding="5" width="100%">'
        '<tr><th width="38%">Keys</th><th>What they do</th></tr>'
    )
    for keys, what in CANVAS_KEYS:
        rows.append(
            "<tr><td><span class='key'>%s</span></td><td>%s</td></tr>"
            % (html.escape(keys), html.escape(what))
        )
    rows.append("</table>")
    rows.append("<h2>In other windows</h2>")
    rows.append(
        '<table class="grid" cellspacing="0" cellpadding="5" width="100%">'
        '<tr><th width="38%">Keys</th><th>What they do</th></tr>'
    )
    for keys, what in other_window_keys():
        rows.append(
            "<tr><td><span class='key'>%s</span></td><td>%s</td></tr>"
            % (html.escape(keys), html.escape(what))
        )
    rows.append("</table>")
    return "\n".join(rows)


def other_window_keys() -> list:
    """The keys of the windows that are not the editor's commands (the manual, the Bitmap
    Editor and the magnifier, the Simulate window), as Settings > Keyboard has them now."""
    from ..editor.view_consistency import bare_zoom_keys, help_reset_key

    bare = ", + and -" if bare_zoom_keys() else ""
    out = [
        ("Ctrl+=, Ctrl+-", "Larger and smaller text in the manual"),
        (help_reset_key(), "Text size 100 % in the manual"),
        ("Ctrl+=, Ctrl+-" + bare, "Zoom in and out in the Bitmap Editor and the magnifier"),
    ]
    try:
        from ..prefs import keymap

        eff, _notes = keymap.sim_resolve()
        for cid, label, _k in keymap.SIM_COMMANDS:
            keys = eff.get(cid, ([], ""))[0]
            if keys:
                out.append(
                    (
                        ", ".join(keymap.native(k) for k in keys),
                        "Simulate window: %s" % label.replace("…", ""),
                    )
                )
    except Exception:  # noqa: BLE001 -- swallow-ok: the list is a convenience
        pass
    return out
