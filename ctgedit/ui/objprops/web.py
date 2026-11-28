"""Web Content > Appearance > General."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ...format import webcontent as W
from ..style.theme import set_note_role
from ...infra.logsetup import note_exception as _note_exc

HELP_TOPIC = "properties#web-content"


class WebContentEditor:
    """The Web Content page; Apply = EditableDocument.set_web_content."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._ro = False
        self._initial = ("", False)
        self.widget = self._build()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TOPIC)
        except RuntimeError:
            pass
        self.reload()

    def _build(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        box = QtWidgets.QGroupBox("URL")
        bl = QtWidgets.QVBoxLayout(box)
        from .scroll import LinesEdit

        # a long address wraps: two lines to start with, more in a taller window
        self.url = LinesEdit(2)
        self.url.setTabChangesFocus(True)
        self.url.setPlaceholderText("https://www.example.com/")
        self.url.setToolTip(
            "The address of the page to show, protocol included (at most %d characters)" % W.MAX_URL
        )
        self.url.textChanged.connect(self._changed)
        bl.addWidget(self.url, 1)
        self.advice = QtWidgets.QLabel()
        self.advice.setWordWrap(True)
        set_note_role(self.advice, "warn")
        self.advice.hide()
        bl.addWidget(self.advice)
        lay.addWidget(box, 1)
        opt = QtWidgets.QGroupBox("Options")
        ol = QtWidgets.QVBoxLayout(opt)
        self.enable_print = QtWidgets.QCheckBox("Enable 'Print' in context menu")
        self.enable_print.setToolTip("Let the operator print the page from its context menu")
        self.enable_print.toggled.connect(lambda _v: self._changed())
        ol.addWidget(self.enable_print)
        lay.addWidget(opt)
        warn = QtWidgets.QLabel(W.LOSS_OF_CONTROL)
        warn.setWordWrap(True)
        set_note_role(warn, "danger")
        warn.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(warn)
        note = QtWidgets.QLabel("At runtime a Web Content object is drawn on top of the other objects.")
        note.setWordWrap(True)
        set_note_role(note, "text_dim")
        lay.addWidget(note)
        return w

    def values(self):
        return self.url.toPlainText().replace("\r", "").replace("\n", ""), self.enable_print.isChecked()

    def _changed(self) -> None:
        url, _p = self.values()
        tips = W.url_advice(url)
        self.advice.setText("\n".join(tips))
        self.advice.setVisible(bool(tips))

    # -- the sheet's editor contract ------------------------------------------------------
    def problem(self):
        url, _p = self.values()
        if (url, _p) == self._initial:
            return None
        why = W.url_problem(url)
        return (why, self.url) if why else None

    def apply(self) -> bool:
        if self._ro:
            return False
        url, pr = self.values()
        if (url, pr) == self._initial:
            return False
        s = self.sheet
        kw = {}
        if url != self._initial[0]:
            kw["url"] = url
        if pr != self._initial[1]:
            kw["enable_print"] = pr
        try:
            return bool(s._doc.set_web_content(s._index, **kw))
        except ValueError as exc:
            s._fail(str(exc), self.url)
            return False

    def reload(self) -> None:
        s = self.sheet
        try:
            info = s._doc.web_content(s._index)
        except Exception:  # noqa: BLE001
            _note_exc("objprops.web.reload")
            info = {"url": "", "enable_print": False}
        self.url.setPlainText(info["url"])
        self.enable_print.setChecked(bool(info["enable_print"]))
        self._initial = self.values()
        self._changed()

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        self.url.setReadOnly(ro)
        self.enable_print.setEnabled(not ro)


def build_general(sheet, page):
    ed = WebContentEditor(sheet)
    sheet.add_editor(ed)
    sheet.web_editor = ed
    return ed.widget


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"general": build_general}
SUBS: list = []
