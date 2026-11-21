"""Cicode Object > General."""

from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtGui, QtWidgets

from .expression import wizard_button

HELP_TOPIC = "properties#cicode-object"
MAX_COMMAND = 254
DEFAULT_LABEL = "<default>"


def _thumbnail(doc, symbol: str) -> Optional[QtGui.QPixmap]:
    """The symbol's library thumbnail (the f(x) icon for the default)."""
    if not symbol:
        try:
            from ...render.ctres import class_icon

            img = class_icon("ADVSHOW")
            return QtGui.QPixmap.fromImage(img) if img is not None else None
        except Exception:  # noqa: BLE001
            return None
    lib, _dot, elem = symbol.partition(".")
    try:
        res = doc._resolver()
        libs = res.libraries(lib, "ctl") if res is not None else []
    except Exception:  # noqa: BLE001
        libs = []
    from ...format.pagesummary import read_element_summary

    for L in libs:
        es = read_element_summary(L.path, elem)
        if es is not None and es.ok and es.has_thumbnail():
            pm = QtGui.QPixmap()
            if pm.loadFromData(es.thumbnail_png(), "PNG"):
                return pm
    return None


def symbol_problem(doc, symbol: str) -> Optional[str]:
    """Why ``symbol`` cannot be the object's display (None = it can): it
    must name a library symbol on the page's include path whose element is
    a bitmap (CtDraw's Set)."""
    if not symbol:
        return None
    lib, _dot, elem = symbol.partition(".")
    if not lib or not elem:
        return "'%s' is not a library symbol name (library.symbol)." % symbol
    from ...format import placement

    try:
        res = doc._resolver()
        info = placement.element_info(res, lib, elem, "ctl") if res is not None else None
    except Exception:  # noqa: BLE001
        info = None
    if info is None:
        return "The symbol %s is not in a library of this project or its include chain." % symbol
    if info.get("root_tc") != 0x07:
        return "The symbol must be a bitmap object: %s is not." % symbol
    return None


class CicodeEditor:
    """The Cicode page; Apply = EditableDocument.set_cicode_object."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._ro = False
        self._syncing = False
        self._initial = ("", "")
        self._symbol = ""
        self.widget = self._build()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TOPIC)
        except Exception:  # noqa: BLE001
            pass
        self.reload()

    def _build(self) -> QtWidgets.QWidget:
        s = self.sheet
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        box = QtWidgets.QGroupBox("Command")
        bl = QtWidgets.QHBoxLayout(box)
        from .scroll import LinesEdit

        self.command = LinesEdit(3)  # grows with the window
        self.command.setTabChangesFocus(True)
        self.command.setPlaceholderText("Cicode command, run while the page is shown")
        self.command.setToolTip(
            "Cicode command, run while the page is shown. Empty: an "
            "animation point. At most %d characters." % MAX_COMMAND
        )
        self.command.textChanged.connect(self._changed)
        bl.addWidget(self.command, 1)
        col = QtWidgets.QVBoxLayout()
        self.wizard = wizard_button(self.command, s.expression_context)
        col.addWidget(self.wizard)
        col.addStretch(1)
        bl.addLayout(col)
        lay.addWidget(box, 1)
        from ..style import theme
        from ..pick_fields import LibraryElementField

        self.count = theme.note_label("")
        lay.addWidget(self.count)

        disp = QtWidgets.QGroupBox("Display as")
        dl = QtWidgets.QVBoxLayout(disp)
        pdir = os.path.dirname(os.path.abspath(s._doc.path)) if s._doc.path else None
        self.display = LibraryElementField(
            pdir,
            "symbol",
            disp,
            title="Select Symbol",
            clear_text="Use &Default",
            empty_text=DEFAULT_LABEL,
            resolve=self._symbol_path,
            validate=self._symbol_problem,
        )
        self.display.btn_browse.setToolTip(
            "Show a library symbol (a bitmap) for this object instead of the f(x) icon"
        )
        self.display.btn_clear.setToolTip("Show the default Cicode object icon")
        self.display.changed.connect(self._display_changed)
        dl.addWidget(self.display)
        # the field's parts (callers and tests read them here)
        self.thumb, self.symbol_label = self.display.picture, self.display.name
        self.btn_set, self.btn_default = self.display.btn_browse, self.display.btn_clear
        lay.addWidget(disp)
        crow = QtWidgets.QHBoxLayout()
        crow.addStretch(1)
        self.btn_clear = QtWidgets.QPushButton("Clear Property")
        self.btn_clear.setToolTip("Empty the command and reset the display (applied with Apply / OK)")
        self.btn_clear.clicked.connect(self.clear)
        crow.addWidget(self.btn_clear)
        lay.addLayout(crow)
        return w

    # -- values ---------------------------------------------------------------------
    def values(self):
        return self.command.toPlainText().replace("\r\n", "\n").replace("\n", "\r\n"), self._symbol

    def set_symbol(self, symbol: str) -> None:
        self._symbol = (symbol or "").strip()
        if self._symbol.lower() in ("cicode.default", DEFAULT_LABEL):
            self._symbol = ""
        lib, dot, elem = self._symbol.partition(".")
        self.display.blockSignals(True)
        try:
            self.display.set_value((lib, elem) if dot else None)
        finally:
            self.display.blockSignals(False)
        if not self._symbol:
            self.display.set_default_picture(_thumbnail(self.sheet._doc, ""))
        self._changed()

    def _symbol_path(self, library: str, element: str):
        """The library file of ``library.element`` on the page's include path."""
        try:
            res = self.sheet._doc._resolver()
            for lib in res.libraries(library, "ctl") if res is not None else []:
                if element.lower() in {n.lower() for n in lib.element_names()}:
                    return lib.path
        except Exception:  # noqa: BLE001 -- no thumbnail then
            return None
        return None

    def _symbol_problem(self, library: str, element: str, _path) -> str:
        return symbol_problem(self.sheet._doc, "%s.%s" % (library, element)) or ""

    def _display_changed(self) -> None:
        v = self.display.value()
        self.set_symbol("%s.%s" % v if v else "")

    def _pick_symbol(self) -> None:
        if not self._ro:
            self.display.browse()

    def clear(self) -> None:
        if self._ro:
            return
        self.command.setPlainText("")
        self.set_symbol("")

    def _changed(self) -> None:
        from ..style import theme

        n = len(self.values()[0])
        self.count.setText("%d of %d characters" % (n, MAX_COMMAND))
        theme.set_note_role(self.count, "danger" if n > MAX_COMMAND else "text_dim")
        s = self.sheet
        if not self._syncing and not s._syncing:
            s._refresh_marks(s._pending_props())

    # -- the sheet's editor contract --------------------------------------------------------
    def pending_props(self, props: dict) -> None:
        cmd, sym = self.values()
        if (cmd, sym) == self._initial:
            return
        if cmd:
            props["CMD"] = cmd
        else:
            props.pop("CMD", None)

    def problem(self):
        cmd, sym = self.values()
        if (cmd, sym) == self._initial:
            return None
        if len(cmd) > MAX_COMMAND:
            return (
                "The command is %d characters long; a Cicode object takes at most %d. "
                "Call a Cicode function for longer commands." % (len(cmd), MAX_COMMAND),
                self.command,
            )
        if sym != self._initial[1]:
            why = symbol_problem(self.sheet._doc, sym)
            if why:
                return why, self.btn_set
        return None

    def apply(self) -> bool:
        if self._ro:
            return False
        cmd, sym = self.values()
        if (cmd, sym) == self._initial:
            return False
        s = self.sheet
        kw = {}
        if cmd != self._initial[0]:
            kw["command"] = cmd
        if sym != self._initial[1]:
            kw["symbol"] = sym
        try:
            return bool(s._doc.set_cicode_object(s._index, **kw))
        except ValueError as exc:
            s._fail(str(exc), self.command)
            return False

    def reload(self) -> None:
        s = self.sheet
        try:
            info = s._doc.cicode_object(s._index)
        except Exception:  # noqa: BLE001
            info = {"command": "", "symbol": ""}
        self._syncing = True
        try:
            self.command.setPlainText(info["command"].replace("\r\n", "\n"))
            self.set_symbol(info["symbol"])
        finally:
            self._syncing = False
        self._initial = self.values()
        self._changed()

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        self.command.setReadOnly(ro)
        self.display.set_read_only(ro)
        for b in (self.wizard, self.btn_default, self.btn_clear):
            b.setEnabled(not ro)


def build_general(sheet, page):
    ed = CicodeEditor(sheet)
    sheet.add_editor(ed)
    sheet.cicode_editor = ed
    return ed.widget


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"general": build_general}
SUBS: list = []
