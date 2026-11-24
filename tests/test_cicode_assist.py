"""The Cicode help of the expression and command fields: colours, underlines, argument
hints, hover help and function completion (ui/objprops/cicode_assist.py)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from qtkit import app as _app

from ctgedit import cicode_lex as L
from ctgedit.io import cicode_catalog as catalog
from ctgedit.ui.objprops import cicode_assist as CA

PAGE_DISPLAY = catalog.CicodeFunction(
    "PageDisplay",
    "INT PageDisplay(STRING Page [, STRING ClusterName])",
    "Displays a graphics page.",
    "Page Display",
    "built-in",
    "(Page [, ClusterName])",
)


@pytest.fixture
def ctx(fx_install, monkeypatch):
    """FxMain's names and functions, with one built-in."""
    from ctgedit.ui.objprops.expression import ExpressionContext

    _app()
    monkeypatch.setattr(catalog, "builtin_functions", lambda: (PAGE_DISPLAY,))
    c = ExpressionContext(fx_install.project("FxMain"))
    # (the field reads them in the background; a test waits for them)
    c.tag_index()
    for t in list(catalog._PRELOADING):
        t.join(30)
    return c


def _field(ctx=None, kind="expr", text=""):
    from ctgedit.ui.objprops.expression import ExpressionField

    _app()
    f = ExpressionField(ctx, field_kind=kind)
    f.setText(text)
    return f


def _roles(f):
    """(role, level) runs of a field: [(text, role, level)]."""
    text = f.inner_text()
    return [(text[s : s + n], k[0], k[1]) for s, n, k in CA.runs(f.assist.keys())]


# ------------------------------------------------------------------ colours
def test_a_field_colours_its_cicode():
    f = _field(text='IF Run THEN Message("on") END')
    assert _roles(f) == [
        ("IF", "keyword", None),
        ("Run", "name", None),
        ("THEN", "keyword", None),
        ("Message", "function", None),
        ('"on"', "string", None),
        ("END", "keyword", None),
    ]
    # a name field (a genie's tag prefix) has no assist
    assert _field(kind="tag").assist is None


def test_what_the_compiler_refuses_is_underlined_as_an_error():
    f = _field(text="A == 1")
    assert ("==", None, "error") in _roles(f)
    assert "no operator ==" in f.assist.problems()[0].message


def test_colours_follow_the_option(monkeypatch):
    f = _field(text='Message("on")')
    monkeypatch.setattr(CA, "option", lambda key: key != "colour_cicode")
    f.assist.refresh()
    assert _roles(f) == []


def test_recolouring_keeps_the_text_the_cursor_and_the_signals():
    from PySide6 import QtWidgets

    f = _field(text='Message("x")')
    f.setCursorPosition(3)
    changed = []
    f.textChanged.connect(changed.append)
    f.assist.refresh()
    f.setReadOnly(True)
    f.assist.refresh()
    assert changed == [] and f.cursorPosition() == 3 and f.inner_text() == 'Message("x")'
    assert f.isReadOnly()
    box = QtWidgets.QPlainTextEdit('Foo("x") // y')
    a = CA.CicodeAssist(box, None, "command")
    moved = []
    box.textChanged.connect(lambda: moved.append(1))
    a.refresh()
    assert moved == [] and not box.document().isModified()
    formats = box.document().firstBlock().layout().formats()
    assert [(r.start, r.length) for r in formats] == [(0, 3), (4, 3), (9, 4)]


def test_the_colours_read_on_every_surface():
    from ctgedit.ui.style import syntax
    from ctgedit.ui.style import theme as T

    _app()
    for scheme, table in (("dark", syntax.DARK), ("light", syntax.LIGHT)):
        c = T._scheme_colours(scheme)
        for role, colour in table.items():
            for surface in ("bg", "panel", "hover", "card", "field", "raised"):
                assert T.contrast(colour, c[surface]) >= 4.5, (scheme, role, surface)
    assert set(syntax.DARK) == set(syntax.LIGHT) == set(syntax.ROLE.values())


# ------------------------------------------------------------------ names and functions
def test_unknown_names_and_functions_are_underlined(ctx):
    f = _field(ctx, text="FX_PUMP1 AND NoSuchTag AND NoSuchFn(1) AND FxToggle(FX_PUMP2)")
    f.assist.look_up_names()
    got = [(p.message, p.level) for p in f.assist.problems()]
    assert got == [
        ("'NoSuchTag' is not a tag in the project.", "warning"),
        ("'NoSuchFn' is not a known Cicode function.", "warning"),
    ]
    assert ("NoSuchTag", "name", "warning") in _roles(f)


def test_names_a_genie_makes_and_key_arguments_are_not_looked_up(ctx):
    f = _field(ctx, text="%Tag%_PV + Area%N%.Level + ?INT 2?")
    f.assist.look_up_names()
    assert f.assist.problems() == []
    f = _field(ctx, kind="command", text="FxSet(FX_PUMP1, Arg1)")
    f.assist.look_up_names()
    assert f.assist.problems() == []


def test_genie_text_reads_as_the_genie_reads_it(ctx):
    """A default may hold quotes; a part of a name the genie makes says so on hover; no
    tag is offered for a parameter's name or a part of a name the genie makes."""
    text = '%Line1%_%Line2%_%Line3=" Hello"% + Area%N%.Level'
    f = _field(ctx, text=text)
    f.assist.look_up_names()
    assert f.assist.problems() == [] and f.problems() == []
    assert ('%Line3=" Hello"%', "param") in [(text[t.start : t.end], t.kind) for t in f.assist.tokens()]
    hover = f.assist.hover_text(text.index("Line3"))
    assert "%Line3% is a genie parameter" in hover and "Its default is &quot; Hello&quot;." in hover
    assert "part of a name the genie makes" in f.assist.hover_text(text.index("Area"))
    # (the name typed starts after the '%', the parameter, in the comment, in the format)
    for typed, start in (
        ("%FX_P", 1),
        ("%Tag%_FX", 5),
        ("?FX_P", 1),
        ("FX_PUMP1 ! FX_P", 11),
        ("FX_PUMP1:##", 9),
    ):
        f.setText(typed)
        assert f._no_names_at(start), typed
    f.setText("1 + FX_P")
    assert not f._no_names_at(4)


def test_the_compilers_errors_are_underlined_at_their_column(ctx):
    class Checker:
        def check(self, text, kind, callback):
            callback({"ok": False, "errors": [{"message": "Function 'Fxx' not found", "column": 5}]})

    f = _field(ctx, text="1 + Fxx(2)")
    f.set_checker(Checker())
    f.check_now()
    assert [(p.start, p.end, p.level) for p in f.assist.problems() if "Fxx'" in p.message] == [
        (4, 7, "error")
    ]
    f.setText("1 + Fxy(2)")  # (the verdict was the old text's)
    assert not any("Fxx'" in p.message for p in f.assist.problems())


# ------------------------------------------------------------------ the argument hint
def test_the_hint_shows_the_argument_being_typed(ctx):
    f = _field(ctx, text='PageDisplay("Main", ')
    f.setCursorPosition(len(f.inner_text()))
    hint = f.assist.hint_html()
    assert "<b>[STRING ClusterName]</b>" in hint and "Displays a graphics page." in hint
    f.setText("FxSet(1, 2, 3")
    f.setCursorPosition(len(f.inner_text()))
    hint = f.assist.hint_html()
    assert hint.startswith("FxSet(INT tag, INT value)") and "FxSet takes 2 arguments." in hint
    f.setText("NoSuch(1")
    assert f.assist.hint_html() == ""


def test_the_hint_shows_above_the_focused_field_and_esc_hides_it(ctx):
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtTest import QTest

    win = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(win)
    f = _field(ctx)
    lay.addWidget(f)
    win.show()
    win.activateWindow()
    f.setFocus()
    QtWidgets.QApplication.processEvents()
    if not f.hasFocus():
        pytest.skip("no keyboard focus on this platform")
    QTest.keyClicks(f, "FxToggle(")
    assert f.assist.hint_visible()
    QTest.keyClick(f, QtCore.Qt.Key_Escape)
    assert not f.assist.hint_visible()
    QTest.keyClicks(f, "1")  # the same call: it stays hidden
    assert not f.assist.hint_visible()
    QTest.keyClick(f, QtCore.Qt.Key_End)
    QTest.keyClicks(f, ") + FxToggle(")  # another call: it shows again
    assert f.assist.hint_visible()
    f.clearFocus()
    QtWidgets.QApplication.processEvents()
    assert not f.assist.hint_visible()
    win.close()


# ------------------------------------------------------------------ hover help
def test_hover_says_what_a_name_is(ctx):
    text = "FX_PUMP1 + FxToggle(1) + Ghost + %Tag% + TRUE"
    toks = L.tokenize(text)

    def hover(word, problems=()):
        return CA.hover_html(text, toks, text.index(word) + 1, ctx, list(problems))

    tag = hover("FX_PUMP1")
    assert "<b>FX_PUMP1</b>" in tag and "Variable tag, DIGITAL" in tag and "pump 1 running" in tag
    fn = hover("FxToggle")
    assert "INT FxToggle(INT tag)" in fn and "FxMain" in fn
    assert "not a tag in the project" in hover("Ghost")
    assert "genie parameter" in hover("%Tag%")
    assert "TRUE is 1" in hover("TRUE")
    p = CA.Problem(
        text.index("Ghost"),
        text.index("Ghost") + 5,
        "warning",
        "'Ghost' is not a tag in the project.",
        L.NAME,
    )
    assert hover("Ghost", [p]) == "&#x27;Ghost&#x27; is not a tag in the project."


# ------------------------------------------------------------------ completion
def test_functions_complete_with_their_bracket(ctx, fx_install):
    from ctgedit.ui.objprops.expression import completion_model_for

    f = _field(ctx)
    model = completion_model_for(fx_install.project("FxMain"))
    f.set_names(model)
    names = f.completion_names("fxt")
    assert "FxToggle" in names
    row = names.index("FxToggle")
    assert model.data(model.index(row, 1)) == "FxMain function"
    assert names.index("FxToggle") < len(names)
    f.setText("1 + FxTo")
    f.setCursorPosition(len(f.inner_text()))
    f._complete_with("FxToggle")
    assert f.inner_text() == "1 + FxToggle("
    # names first in name order with the functions that start the same way
    assert f.completion_names("fx_p")[:3] == ["FX_PUMP1", "FX_PUMP2", "FX_PUMP3"]
    # a name field offers no functions
    g = _field(ctx, kind="tag")
    g.set_names(model)
    assert "FxToggle" not in g.completion_names("fxt")


def test_a_command_box_gets_colours_and_completion(ctx):
    from PySide6 import QtWidgets
    from PySide6.QtTest import QTest

    from ctgedit.ui.objprops.expression import wizard_button
    from ctgedit.ui.objprops.input import CommandEdit

    box = CommandEdit("FxSet(FX_PUMP1, 1)")
    wizard_button(box, ctx)
    a = box.cicode_assist
    assert wizard_button(box, ctx) is not None and box.cicode_assist is a  # (one per box)
    assert [k for k in CA.runs(a.keys())][0][2] == ("function", None)
    win = QtWidgets.QWidget()
    QtWidgets.QVBoxLayout(win).addWidget(box)
    win.show()
    box.setFocus()
    cur = box.textCursor()
    cur.movePosition(cur.MoveOperation.End)
    box.setTextCursor(cur)
    QTest.keyClicks(box, "; FxTo")
    c = a._completer
    assert c is not None and "FxToggle" in c.model().names()
    a._complete_with("FxToggle")
    assert box.toPlainText() == "FxSet(FX_PUMP1, 1); FxToggle("
    win.close()


def test_refresh_all_survives_deleted_fields(ctx):
    f = _field(ctx, text="FX_PUMP1")
    f.deleteLater()
    from PySide6 import QtCore, QtWidgets

    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    CA.refresh_all()


def test_typing_and_undo_work_in_a_coloured_field():
    from PySide6 import QtWidgets
    from PySide6.QtTest import QTest

    f = _field()
    win = QtWidgets.QWidget()
    QtWidgets.QVBoxLayout(win).addWidget(f)
    win.show()
    f.setFocus()
    QTest.keyClicks(f, 'IF A THEN Message("x") END')
    assert f.inner_text() == 'IF A THEN Message("x") END'
    assert ("Message", "function", None) in _roles(f)
    f.undo()
    assert f.inner_text() == ""
    f.redo()
    assert f.inner_text() == 'IF A THEN Message("x") END'
    win.close()


def test_nothing_waits_for_tags_still_being_read(fx_install, monkeypatch):
    from ctgedit.ui.objprops.expression import ExpressionContext

    _app()
    c = ExpressionContext(fx_install.project("FxMain"))
    monkeypatch.setattr(c, "names_ready", lambda: False)
    f = _field(c, text="NoSuchTag + FX_PUMP1")
    f.assist.look_up_names()
    assert f.assist.problems() == []  # (looked up again when they are read)
    assert f.assist._timer.isActive() and f.assist._timer.interval() == f.assist.LOOKUP_RETRY_MS
    text = "FX_PUMP1"
    assert "still being read" in CA.hover_html(text, L.tokenize(text), 1, c, [])


def test_colours_follow_the_theme_and_a_read_only_field_colours_once():
    from PySide6 import QtWidgets

    from ctgedit.ui.style import syntax, theme

    app = _app()
    theme.apply_theme(app, "light", 1.0)
    f = _field(text='Message("x")')
    f.setReadOnly(True)
    f.show()
    a = f.assist
    calls = []
    real = a._theme_changed
    a._theme_changed = lambda: (calls.append(1), real())
    a.refresh()
    QtWidgets.QApplication.processEvents()
    assert calls == []  # (the read-only switch around the colouring is no theme change)
    assert a._format(("string", None)).foreground().color().name() == syntax.LIGHT["string"]
    theme.apply_theme(app, "dark", 1.0)
    QtWidgets.QApplication.processEvents()
    assert calls and a._format(("string", None)).foreground().color().name() == syntax.DARK["string"]
    assert f.isReadOnly()


# ------------------------------------------------------------------ did you mean
def test_an_unknown_name_offers_the_names_it_may_mean(ctx):
    f = _field(ctx, text="FX_PUMP7 + FxTogle(1)")
    f.assist.look_up_names()
    tag, fn = f.assist.problems()
    assert (tag.unknown, fn.unknown) == (L.NAME, L.FUNCTION)
    assert f.assist.suggestions(L.NAME, "FX_PUMP7")[:3] == ["FX_PUMP1", "FX_PUMP2", "FX_PUMP3"]
    assert CA.did_you_mean(["A"]) == "Did you mean A?"
    assert CA.did_you_mean(["A", "B", "C"]) == "Did you mean A, B or C?"
    assert "Did you mean FX_PUMP1, FX_PUMP2 or FX_PUMP3?" in f.assist.hover_text(2)
    tip = f.assist.hover_text(f.inner_text().index("FxTogle") + 1)
    assert tip == "&#x27;FxTogle&#x27; is not a known Cicode function. Did you mean FxToggle?"
    # the menu replaces it (one undo step)
    acts = f.assist.menu_actions(f.inner_text().index("FxTogle") + 2)
    assert [a[0] for a in acts] == ["Replace with FxToggle"]
    acts[0][1]()
    assert f.inner_text() == "FX_PUMP7 + FxToggle(1)"
    f.undo()
    assert f.inner_text() == "FX_PUMP7 + FxTogle(1)"
    assert CA.suggestions(ctx, L.NAME, "Zzzzzzzz") == []


def test_the_field_menu_holds_the_replacements_and_the_larger_box(ctx):
    from PySide6 import QtWidgets

    f = _field(ctx, text="FX_PUMP7")
    f.assist.look_up_names()
    menu = QtWidgets.QMenu()
    menu.addAction("Copy")
    f.assist.add_to_menu(menu, 2)
    assert [a.text() for a in menu.actions()][:3] == [
        "Replace with FX_PUMP1",
        "Replace with FX_PUMP2",
        "Replace with FX_PUMP3",
    ]
    f.setCursorPosition(2)
    texts = [a.text() for a in f.context_menu(f.cursorRect().center()).actions()]
    assert texts[0] == "Replace with FX_PUMP1" and "Edit in a Larger Box…" in texts


# ------------------------------------------------------------------ go to definition
def test_a_project_function_opens_at_its_line(ctx, monkeypatch):
    from PySide6 import QtCore, QtGui

    fn = ctx.function("FxToggle")
    assert fn.path.lower().endswith("fxmain.ci") and fn.line == 2
    opened = []
    monkeypatch.setattr(CA, "open_definition", lambda f: opened.append((f.name, f.line)) or True)
    f = _field(ctx, text="FxToggle(1) + PageDisplay(1)")
    assert f.assist.definition_at(f.inner_text().index("PageDisplay") + 1) is None  # (built-in)
    assert [a[0] for a in f.assist.menu_actions(2)] == ["Go to Definition"]
    assert "Ctrl+click or F12 opens it at line 2." in f.assist.hover_text(1)
    f.setCursorPosition(2)
    f.assist.eventFilter(f, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_F12, QtCore.Qt.NoModifier))
    at = QtCore.QPointF(f.cursorRect().center())
    click = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        at,
        at,
        QtCore.Qt.LeftButton,
        QtCore.Qt.LeftButton,
        QtCore.Qt.ControlModifier,
    )
    assert f.assist.eventFilter(f, click) is True
    assert opened == [("FxToggle", 2), ("FxToggle", 2)]


def test_open_definition_runs_vs_code_with_the_line(ctx, monkeypatch):
    runs = []
    monkeypatch.setattr(CA, "_vscode", lambda: r"C:\VS Code\bin\code.cmd")
    monkeypatch.setattr(CA.subprocess, "Popen", lambda args, **kw: runs.append(args))
    fn = ctx.function("FxToggle")
    assert CA.open_definition(fn)
    assert runs == [[r"C:\VS Code\bin\code.cmd", "--goto", "%s:2" % fn.path]]
    assert not CA.open_definition(catalog.CicodeFunction("Nowhere"))


# ------------------------------------------------------------------ display formats
def test_format_preview():
    assert CA.format_preview("###.# EU", 45.67, "m") == " 45.7 m"
    assert CA.format_preview("0###", 45.67) == "0046"
    assert CA.format_preview("-####.##", 45.67) == "45.67  "
    assert CA.format_preview("#.##EU", 45.67) == "45.67"
    assert CA.format_preview("5.2", 45.67) is None


def test_the_hint_shows_a_display_format_with_the_tags_value(ctx):
    f = _field(ctx, text="FX_LEVEL1:##.# EU")
    f.setCursorPosition(len(f.inner_text()))
    hint = f.assist.format_hint()
    assert "Display format" in hint and "A value of FX_LEVEL1 shows as" in hint and "[45.7]" in hint
    f.setText("FX_LEVEL1:")
    f.setCursorPosition(len(f.inner_text()))
    assert "For example ###.# EU shows [ 45.7 m]." in f.assist.format_hint()
    f.setText("FX_LEVEL1:##")
    f.setCursorPosition(len(f.inner_text()))
    assert f.assist.format_hint()
    assert f._no_names_at(len("FX_LEVEL1:"))  # (nothing to complete in a format)
    f.setText("Message(FX_LEVEL1)")
    f.setCursorPosition(8)
    assert f.assist.format_hint() == ""


# ------------------------------------------------------------------ live values
def test_hover_shows_the_value_in_the_simulation(ctx, monkeypatch):
    values = {"FX_PUMP1": "1"}
    monkeypatch.setattr(ctx, "live_value", lambda name: values.get(name, ""), raising=False)
    f = _field(ctx, text="FX_PUMP1 + FX_PUMP2")
    assert "Value in the simulation: <b>1</b>" in f.assist.hover_text(1)
    assert "Value in the simulation: being read" in f.assist.hover_text(f.inner_text().index("FX_PUMP2"))


def test_live_value_asks_the_simulation(fx_install):
    from ctgedit.ui.objprops.expression import ExpressionContext

    class Tools:
        def __init__(self):
            self.calls = []

        def available(self):
            return True

        def outcome(self, call):
            return {"value": 42} if self.calls else None

        def call(self, call):
            self.calls.append(call)

    t = Tools()
    c = ExpressionContext(fx_install.project("FxMain"), live=lambda: t)
    assert c.live_value("FX_PUMP1") == ""
    assert c.live_value("FX_PUMP1") == "42"
    assert t.calls == ['TagRead("FX_PUMP1")'] * 2
    assert ExpressionContext(None).live_value("X") is None


# ------------------------------------------------------------------ equipment
def test_equipment_completes_level_by_level(ctx, fx_install):
    from ctgedit.ui.objprops.expression import completion_model_for

    idx = ctx.tag_index()
    kids = idx.equipment_children("fxplant.")
    assert kids[:4] == [
        ("FxPlant.Mode", "item"),
        ("FxPlant.Pump1", "equipment"),
        ("FxPlant.Pump2", "equipment"),
        ("FxPlant.Pump3", "equipment"),
    ]
    assert ("FxPlant.Tank1", "equipment") in kids and ("FxPlant.Valve8", "equipment") in kids
    assert len(kids) == len(set(kids))
    assert idx.equipment_children("FxPlant.Pump1.") == [
        ("FxPlant.Pump1.Run", "item"),
        ("FxPlant.Pump1.Speed", "item"),
    ]
    assert idx.equipment_children("Nothing.") == []
    f = _field(ctx)
    model = completion_model_for(fx_install.project("FxMain"))
    f.set_names(model)
    assert f.completion_names("FxPlant.Ta") == ["FxPlant.Tank1", "FxPlant.Tank2", "FxPlant.Tank3"]
    assert model.data(model.index(0, 1)) == "Equipment"
    f.setText("FxPlant.Ta")
    f.setCursorPosition(len(f.inner_text()))
    f._complete_with("FxPlant.Tank1")
    assert f.inner_text() == "FxPlant.Tank1."
    assert f.completion_names("FxPlant.Tank1.") == ["FxPlant.Tank1.Level", "FxPlant.Tank1.Temp"]
    assert model.data(model.index(0, 1)) == "Equipment item"


# ------------------------------------------------------------------ the larger box
def test_the_larger_box_edits_the_field(ctx, monkeypatch):
    from PySide6 import QtWidgets

    from ctgedit.ui.objprops import cicode_box as B

    assert B.one_line("A AND\n  B\n\nOR C") == "A AND B OR C"
    assert B.comment_problem("A ! note\nAND B")
    assert B.comment_problem("A /* note */\nAND B") == ""
    assert B.comment_problem("A AND B ! note") == ""
    f = _field(ctx, text="FX_PUMP1")
    finished = []
    f.editingFinished.connect(lambda: finished.append(1))

    def run(dlg):
        assert dlg.edit.toPlainText() == "FX_PUMP1" and dlg.assist.context is ctx
        dlg.edit.setPlainText("FX_PUMP1 AND\nFX_PUMP2")
        return True

    monkeypatch.setattr(B.CicodeBoxDialog, "exec", run)
    assert f.edit_in_box()
    assert f.inner_text() == "FX_PUMP1 AND FX_PUMP2" and finished
    dlg = B.CicodeBoxDialog("A ! x\nB", ctx)
    assert not dlg.buttons.button(QtWidgets.QDialogButtonBox.Ok).isEnabled()
    assert "/* */" in dlg.note.text()


def test_the_watch_boxes_are_cicode_fields(fx_install):
    from ctgedit.ui.objprops.expression import ExpressionField
    from ctgedit.ui.simulate.watch_panel import WatchPanel

    _app()
    w = WatchPanel()
    assert isinstance(w.add_edit, ExpressionField) and isinstance(w.call_edit, ExpressionField)
    page = os.path.join(fx_install.project("FxMain"), "FxCicode.ctg")
    w.set_source(None, page)
    assert w.context.project_dir == fx_install.project("FxMain")
    assert w.add_edit.assist.context is w.context and w.call_edit.assist.context is w.context
    assert w.context.live_value("FX_PUMP1") is None  # (no simulation)


def test_a_box_being_deleted_raises_nothing(ctx, capfd):
    from PySide6 import QtWidgets

    from ctgedit.ui.objprops.cicode_box import CicodeBoxDialog

    dlg = CicodeBoxDialog("IF X THEN\n  Y()\nEND", ctx)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    dlg.deleteLater()
    QtWidgets.QApplication.processEvents()
    assert "already deleted" not in capfd.readouterr().err
