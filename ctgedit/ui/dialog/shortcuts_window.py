"""Help > Keyboard Shortcuts."""

from __future__ import annotations

import html
import os
from typing import Dict, List, Set

from PySide6 import QtCore, QtGui, QtWidgets

from ..prefs import keymap
from ..prefs.keymap_page import ID_ROLE, KEYS_ROLE, KeyCapDelegate, KeySearchBar

#: the menus of the list at the left ('' = every command)
CATEGORIES = (
    ("All", ""),
    ("File", "File"),
    ("Edit", "Edit"),
    ("View", "View"),
    ("Text", "Text"),
    ("Arrange", "Arrange"),
    ("Tools", "Tools"),
    ("Canvas", "Canvas"),
)

_MODS = {"Ctrl", "Shift", "Alt", "Meta", "Win"}

# the keyboard picture: rows of (key name, width in key units); '' = a gap
_ROWS = (
    (
        ("Esc", 1),
        ("", 0.5),
        ("F1", 1),
        ("F2", 1),
        ("F3", 1),
        ("F4", 1),
        ("", 0.5),
        ("F5", 1),
        ("F6", 1),
        ("F7", 1),
        ("F8", 1),
        ("", 0.5),
        ("F9", 1),
        ("F10", 1),
        ("F11", 1),
        ("F12", 1),
    ),
    (
        ("`", 1),
        ("1", 1),
        ("2", 1),
        ("3", 1),
        ("4", 1),
        ("5", 1),
        ("6", 1),
        ("7", 1),
        ("8", 1),
        ("9", 1),
        ("0", 1),
        ("-", 1),
        ("=", 1),
        ("Backspace", 2),
    ),
    (
        ("Tab", 1.5),
        ("Q", 1),
        ("W", 1),
        ("E", 1),
        ("R", 1),
        ("T", 1),
        ("Y", 1),
        ("U", 1),
        ("I", 1),
        ("O", 1),
        ("P", 1),
        ("[", 1),
        ("]", 1),
        ("\\", 1.5),
    ),
    (
        ("Caps", 1.75),
        ("A", 1),
        ("S", 1),
        ("D", 1),
        ("F", 1),
        ("G", 1),
        ("H", 1),
        ("J", 1),
        ("K", 1),
        ("L", 1),
        (";", 1),
        ("'", 1),
        ("Enter", 2.25),
    ),
    (
        ("Shift", 2.25),
        ("Z", 1),
        ("X", 1),
        ("C", 1),
        ("V", 1),
        ("B", 1),
        ("N", 1),
        ("M", 1),
        (",", 1),
        (".", 1),
        ("/", 1),
        ("Shift", 2.75),
    ),
    (
        ("Ctrl", 1.5),
        ("Win", 1.25),
        ("Alt", 1.25),
        ("Space", 6.0),
        ("Alt", 1.25),
        ("Menu", 1.25),
        ("Ctrl", 1.5),
    ),
)
_NAV = (
    (("Ins", 1), ("Home", 1), ("PgUp", 1)),
    (("Del", 1), ("End", 1), ("PgDown", 1)),
    (),
    (("", 1), ("Up", 1), ("", 1)),
    (("Left", 1), ("Down", 1), ("Right", 1)),
)
#: key names as the key text says them -> the key of the picture
_ALIASES = {
    "Return": "Enter",
    "+": "=",
    "Num": "",
    "Delete": "Del",
    "Insert": "Ins",
    "PgDn": "PgDown",
    "Page Up": "PgUp",
    "Page Down": "PgDown",
    "Escape": "Esc",
    "Backtab": "Tab",
    "*": "8",
    "Plus": "=",
}


def main_key(key: str) -> str:
    """The key of the picture a key ends with ('Ctrl+Shift+Z' -> 'Z')."""
    text = keymap.norm(key)
    parts = [p for p in key_parts_portable(text)]
    if not parts:
        return ""
    last = parts[-1]
    return _ALIASES.get(last, last.upper() if len(last) == 1 else last)


def key_parts_portable(key: str) -> List[str]:
    parts, cur = [], ""
    for ch in key:
        if ch == "+" and cur:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def modifiers_of(key: str) -> Set[str]:
    return {p for p in key_parts_portable(keymap.norm(key))[:-1] if p in _MODS}


class KeyboardPicture(QtWidgets.QWidget):
    """A small keyboard; the keys of the rows listed are lit (accent), their modifiers
    outlined. ``keyClicked(name)`` when a key is clicked."""

    keyClicked = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(110)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setAccessibleName("Keyboard")
        self.setToolTip("The keys of the commands listed. Click a key to list its commands")
        self._lit: Dict[str, int] = {}
        self._mods: Set[str] = set()
        self._rects: List[tuple] = []

    def sizeHint(self):  # noqa: N802 -- Qt naming
        return QtCore.QSize(560, 150)

    def set_keys(self, keys) -> None:
        lit: Dict[str, int] = {}
        mods: Set[str] = set()
        for k in keys:
            m = main_key(k)
            if m:
                lit[m] = lit.get(m, 0) + 1
            mods |= modifiers_of(k)
        self._lit, self._mods = lit, mods
        self.update()

    def lit(self) -> Set[str]:
        return set(self._lit)

    def _layout(self) -> List[tuple]:
        units_w = 15.0 + 0.5 + 3.0
        units_h = 6.0 + 0.4
        u = min((self.width() - 8) / units_w, (self.height() - 6) / units_h)
        x0 = (self.width() - u * units_w) / 2.0
        y0 = (self.height() - u * units_h) / 2.0
        out = []
        for r, row in enumerate(_ROWS):
            y = y0 + (r * u) + (0.4 * u if r else 0)
            x = x0
            for name, w in row:
                if name:
                    out.append((name, QtCore.QRectF(x + 1, y + 1, w * u - 2, u - 2)))
                x += w * u
        nx = x0 + 15.5 * u
        for r, row in enumerate(_NAV):
            y = y0 + (r + 1) * u + 0.4 * u
            x = nx
            for name, w in row:
                if name:
                    out.append((name, QtCore.QRectF(x + 1, y + 1, w * u - 2, u - 2)))
                x += w * u
        return out

    def paintEvent(self, _e):  # noqa: N802 -- Qt naming
        from ..style.theme import COLORS

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._rects = self._layout()
        edge = QtGui.QColor(COLORS.get("border", "#3a3f46"))
        face = QtGui.QColor(COLORS.get("panel", "#26292e"))
        accent = QtGui.QColor(COLORS.get("accent", "#4da3ff"))
        text = QtGui.QColor(COLORS.get("text_dim", "#969ca5"))
        on_text = QtGui.QColor(COLORS.get("on_selection", "#ffffff"))
        f = QtGui.QFont(self.font())
        f.setPointSizeF(max(5.0, f.pointSizeF() * 0.72))
        p.setFont(f)
        for name, rect in self._rects:
            lit = name in self._lit
            mod = name in self._mods or (name == "Win" and "Meta" in self._mods)
            p.setPen(QtGui.QPen(accent if (lit or mod) else edge, 1.5 if mod else 1))
            p.setBrush(accent if lit else face)
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(on_text if lit else text)
            label = {"Backspace": "Bksp", "PgDown": "PgDn"}.get(name, name)
            p.drawText(rect, QtCore.Qt.AlignCenter, label)
        p.end()

    def key_at(self, pos) -> str:
        for name, rect in self._rects or self._layout():
            if rect.contains(QtCore.QPointF(pos)):
                return name
        return ""

    def mousePressEvent(self, e):  # noqa: N802 -- Qt naming
        name = self.key_at(e.position().toPoint())
        if name and name not in _MODS and name not in ("Caps", "Menu", "Win"):
            self.keyClicked.emit(name)


class ShortcutsWindow(QtWidgets.QDialog):
    """Help > Keyboard Shortcuts (see the module docstring)."""

    RESIZE = "both"

    def __init__(self, window, parent=None):
        super().__init__(parent if parent is not None else window)
        self._win = window
        self.setWindowTitle("Keyboard Shortcuts - ctgedit")
        self._key_name = ""  # a key clicked on the picture
        lay = QtWidgets.QVBoxLayout(self)
        self.bar = KeySearchBar(self, regex_key="Search keyboard shortcuts")
        self.bar.changed.connect(self._filter)
        self.bar.search.bind_find_shortcut(self)
        lay.addWidget(self.bar)
        split = QtWidgets.QHBoxLayout()
        self.categories = QtWidgets.QListWidget()
        self.categories.setAccessibleName("Menus")
        for label, _top in CATEGORIES:
            self.categories.addItem(label)
        fm = self.categories.fontMetrics()
        self.categories.setFixedWidth(max(fm.horizontalAdvance(x) for x, _t in CATEGORIES) + 40)
        self.categories.setCurrentRow(0)
        self.categories.currentRowChanged.connect(lambda _r: self._filter())
        split.addWidget(self.categories)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Command", "Menu", "Keys"])
        self.table.setAccessibleName("Keyboard shortcuts")
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setItemDelegateForColumn(2, KeyCapDelegate(self.table))
        # (a double-click changes the keys, as in Settings > Keyboard: a key reference never
        # runs Delete on the page; Run Command does that on purpose)
        self.table.itemDoubleClicked.connect(lambda _i: self.change_current())
        self.table.currentCellChanged.connect(lambda *_a: self._sync_buttons())
        from ..style.theme import empty_state

        # (a search that hides every row says so, instead of an empty grid)
        self.empty = empty_state("search", "No matching shortcuts.")
        self.table_stack = QtWidgets.QStackedWidget()
        self.table_stack.addWidget(self.table)
        self.table_stack.addWidget(self.empty)
        split.addWidget(self.table_stack, 1)
        lay.addLayout(split, 1)
        self.bar.search.set_list_view(self.table)
        self.only_keys = QtWidgets.QCheckBox("Only commands with keys")
        self.only_keys.setChecked(True)
        self.only_keys.toggled.connect(lambda _on: self._filter())
        lay.addWidget(self.only_keys)
        self.keyboard = KeyboardPicture(self)
        self.keyboard.keyClicked.connect(self._key_clicked)
        lay.addWidget(self.keyboard)
        row = QtWidgets.QHBoxLayout()
        self.btn_change = QtWidgets.QPushButton("Change…")
        self.btn_change.setToolTip("Change the keys of the selected command")
        self.btn_run = QtWidgets.QPushButton("Run Command")
        self.btn_run.setToolTip("Run the selected command in the editor")
        self.btn_print = QtWidgets.QPushButton("Print…")
        self.btn_print.setToolTip("Print a one-page list of the keys")
        self.btn_export = QtWidgets.QPushButton("Export…")
        self.btn_export.setToolTip("Save a one-page list of the keys as a PDF file")
        for b, fn in (
            (self.btn_change, self.change_current),
            (self.btn_run, self.run_current),
            (self.btn_print, self._print),
            (self.btn_export, self._export),
        ):
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self.resize(820, 620)
        # keys changed anywhere (Settings, a preset, an import, another window's Change):
        # listed again once the events settle, or when the window is shown again
        self._stale = False
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)
        keymap.notifier().applied.connect(self._keys_applied)
        self.refresh()
        from .. import tables

        # (the columns always fit the window's width, as in Settings > Keyboard: a key that
        # does not fit ends in '...' and the tool tip names them all)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        tables.make_resizable(
            self.table, "ShortcutsWindow/keys", stretch=0, fit_view=True, caps={1: 0.24, 2: 0.4}, squeeze={1}
        )
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + 10)

    # -- rows -------------------------------------------------------------------------
    def _keys_applied(self, window) -> None:
        try:
            if window is not self._win:
                return
            if self.isVisible():
                self._refresh_timer.start()
            else:
                self._stale = True
        except RuntimeError:  # swallow-ok: this window is being deleted
            return

    def showEvent(self, e):  # noqa: N802 -- Qt naming
        if self._stale:
            self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        self._stale = False
        cur = self.current_id()
        cmds = keymap.commands(self._win, fixed=True)
        eff, _n = keymap.resolve(self._win)
        self.table.setRowCount(len(cmds))
        for r, c in enumerate(cmds):
            keys = [] if c.fixed else eff.get(c.id, ([], ""))[0]
            crumb = c.crumb.replace(" > ", " › ")
            for col, v in enumerate((c.label, crumb, c.what if c.fixed else "")):
                it = QtWidgets.QTableWidgetItem(v)
                it.setData(ID_ROLE, c.id)
                it.setData(
                    QtCore.Qt.UserRole,
                    "Canvas" if (c.fixed or c.scope == "canvas") else c.top().replace("&", ""),
                )
                if col == 2:
                    it.setData(KEYS_ROLE, keys)
                    it.setToolTip(keymap.keys_tip(None if c.fixed else c.action, keys, c.what))
                self.table.setItem(r, col, it)
        self._filter()
        if cur:
            self.select(cur)

    def _key_clicked(self, name: str) -> None:
        self._key_name = "" if name == self._key_name else name
        self._filter()

    def _filter(self) -> None:
        k = self.categories.currentRow()
        top = CATEGORIES[k][1] if 0 <= k < len(CATEGORIES) else ""
        shown_keys = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            keys = self.table.item(r, 2).data(KEYS_ROLE) or []
            fixed = str(item.data(ID_ROLE)).startswith("canvas.")
            texts = [self.table.item(r, c).text() for c in (0, 1, 2)]
            ok = self.bar.matches(texts, keys)
            if ok and top:
                ok = item.data(QtCore.Qt.UserRole) == top
            if ok and self.only_keys.isChecked() and not keys and not fixed:
                ok = False
            if ok and self._key_name:
                ok = any(main_key(x) == self._key_name for x in keys)
            self.table.setRowHidden(r, not ok)
            if ok:
                shown_keys += keys
        self.keyboard.set_keys(shown_keys)
        # (a selected row the search hid is not the one Change or Run acts on)
        r = self.table.currentRow()
        if r >= 0 and self.table.isRowHidden(r):
            self.table.setCurrentCell(-1, -1)
            self.table.clearSelection()
        any_shown = any(not self.table.isRowHidden(r) for r in range(self.table.rowCount()))
        self.table_stack.setCurrentWidget(self.table if any_shown else self.empty)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """Change only for a shown command whose keys can change, Run for a shown command."""
        btn = getattr(self, "btn_change", None)
        if btn is None:
            return
        cid = self.current_id()
        btn.setEnabled(bool(cid) and not cid.startswith("canvas."))
        self.btn_run.setEnabled(bool(cid) and not cid.startswith("canvas."))

    def shown_ids(self) -> List[str]:
        return [
            self.table.item(r, 0).data(ID_ROLE)
            for r in range(self.table.rowCount())
            if not self.table.isRowHidden(r)
        ]

    def current_id(self) -> str:
        r = self.table.currentRow()
        if r < 0 or self.table.item(r, 0) is None or self.table.isRowHidden(r):
            return ""
        return self.table.item(r, 0).data(ID_ROLE) or ""

    def select(self, cid: str) -> bool:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(ID_ROLE) == cid and not self.table.isRowHidden(r):
                self.table.setCurrentCell(r, 0)
                return True
        return False

    # -- commands ---------------------------------------------------------------------
    def change_current(self):
        cid = self.current_id()
        if not cid or cid.startswith("canvas."):
            return None
        from ..prefs.keymap_page import open_change_shortcut

        dlg = open_change_shortcut(self, cid, window=self._win)
        dlg.done_keys.connect(lambda _u: self._refresh_timer.start())
        return dlg

    def run_current(self) -> None:
        cid = self.current_id()
        a = keymap.find_action(self._win, cid) if cid else None
        if a is None:
            return
        if not a.isEnabled():
            self._win.statusBar().showMessage("%s is not available right now." % keymap.label_of(a), 5000)
            return
        QtCore.QTimer.singleShot(0, a, a.trigger)

    # -- the one-page list -------------------------------------------------------------
    def cheat_sheet_html(self, pt: float = 7.0) -> str:
        """The keys of every command that has one, by menu, plus the canvas keys."""
        groups: Dict[str, list] = {}
        order: List[str] = []
        eff, _n = keymap.resolve(self._win)
        for c in keymap.commands(self._win, fixed=True):
            keys = [] if c.fixed else eff.get(c.id, ([], ""))[0]
            if not keys and not c.fixed:
                continue
            top = "On the canvas" if c.fixed else (c.top().replace("&", "") or "Canvas")
            if top not in groups:
                groups[top] = []
                order.append(top)
            ktext = c.what if c.fixed else ", ".join(keymap.native(k) for k in keys)
            groups[top].append((c.label, ktext))
        cols: List[List[str]] = [[], [], []]
        sizes = [0, 0, 0]
        for top in order:
            k = sizes.index(min(sizes))
            rows = groups[top]
            cols[k].append(
                "<h3>%s</h3><table cellspacing='0' cellpadding='1' width='100%%'>" % html.escape(top)
            )
            for label, keys in rows:
                cols[k].append(
                    "<tr><td>%s</td><td align='right'><b>%s</b></td></tr>"
                    % (html.escape(label), html.escape(keys))
                )
            cols[k].append("</table>")
            sizes[k] += len(rows) + 2
        style = (
            "body { font-family: 'Segoe UI'; font-size: %.1fpt; color: #000000; } "
            "h1 { font-size: %.1fpt; margin: 0; } h3 { font-size: %.1fpt; margin: 4px 0 1px 0; } "
            "td { padding: 0 2px; }" % (pt, pt * 1.8, pt * 1.15)
        )
        body = "".join("<td valign='top' width='33%%'>%s</td>" % "".join(c) for c in cols)
        return (
            "<html><head><style>%s</style></head><body><h1>ctgedit keyboard shortcuts"
            "</h1><table width='100%%' cellspacing='8'><tr>%s</tr></table></body></html>" % (style, body)
        )

    def _document(self, printer) -> QtGui.QTextDocument:
        """The list on one page of ``printer`` (the text gets smaller until it fits)."""
        rect = printer.pageLayout().paintRect(QtGui.QPageLayout.Point)
        screen = QtGui.QGuiApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch() if screen is not None else 96.0
        doc = QtGui.QTextDocument()
        pt = 7.5
        while True:
            doc.setHtml(self.cheat_sheet_html(pt))
            # (the page in the document's own pixels: the text is laid out at the screen's)
            doc.setPageSize(QtCore.QSizeF(rect.width() * dpi / 72.0, rect.height() * dpi / 72.0))
            if doc.pageCount() <= 1 or pt <= 4.5:
                return doc
            pt -= 0.5

    def export_pdf(self, path: str) -> bool:
        """Write the one-page list to the PDF file ``path``."""
        P = _print_support()
        printer = P.QPrinter(P.QPrinter.HighResolution)
        printer.setOutputFormat(P.QPrinter.PdfFormat)
        printer.setPageOrientation(QtGui.QPageLayout.Landscape)
        # (written beside it first and put in its place once whole: a file there from before
        # stays as it was when the export fails, and never passes for this one)
        tmp = os.path.splitext(path)[0] + ".~export.pdf"
        printer.setOutputFileName(tmp)
        try:
            self._document(printer).print_(printer)
            if not (os.path.isfile(tmp) and os.path.getsize(tmp) > 0):
                return False
            os.replace(tmp, path)
            return True
        except OSError:  # swallow-ok: the caller says the file could not be written
            return False
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _export(self) -> None:
        start = os.path.join(os.path.expanduser("~"), "ctgedit-keys.pdf")
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Keyboard Shortcuts", start, "PDF files (*.pdf)"
        )
        if path and not self.export_pdf(path):
            QtWidgets.QMessageBox.warning(
                self, "Export Keyboard Shortcuts", "%s could not be written." % path
            )

    def _print(self) -> None:
        P = _print_support()
        printer = P.QPrinter(P.QPrinter.HighResolution)
        printer.setPageOrientation(QtGui.QPageLayout.Landscape)
        dlg = P.QPrintDialog(printer, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self._document(printer).print_(printer)


def _print_support():
    from PySide6 import QtPrintSupport as _P

    return _P
