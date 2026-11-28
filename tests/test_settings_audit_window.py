"""Window settings."""

import os
import shutil
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app

#: FxMain's small page the tabs tests copy (A, B ...)
SMALL = "FxThumb"

KEYS = (
    "ui/new_tab_place",
    "ui/after_close",
    "ui/ask_unpin",
    "ui/tab_width",
    "ui/context_tabs_open",
    "ui/qat",
    "ui/menu_quick_row",
    "ui/hud_buttons",
    "ui/palette_keep_search",
    "ui/quick_open_scope",
    "opt/new_page_name",
    "opt/compile_results",
    "opt/single_instance",
    "opt/explorer_session",
    "opt/an_warn",
)


def _pump(n=6):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _delete_now():
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def _close_all(keep=None):
    from ctgedit.ui.main_window import MainWindow

    for x in list(MainWindow.instances):
        if x is keep:
            continue
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow.instances:
            MainWindow.instances.remove(x)
        x.hide()
        x.deleteLater()
    _delete_now()


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    for k in (
        "auto_placeholder_missing",
        "auto_update_genies",
        "auto_update_symbols",
        "auto_update_template",
    ):
        app_settings().setValue("opt/" + k, False)
    w = MainWindow()
    w.resize(1400, 860)
    yield w
    _close_all()
    # (the module values back at their defaults for the tests after these)
    from ctgedit.ui.prefs import window_options

    st = app_settings()
    for k in KEYS:
        st.remove(k)
    window_options.load()


def _copies(fx, names, src=SMALL):
    """Copies of FxMain's page ``src`` named ``names`` in FxMain's folder (unregistered)."""
    d = fx.project("FxMain")
    out = []
    for n in names:
        p = os.path.join(d, n + ".ctg")
        shutil.copyfile(fx.page(src), p)
        out.append(p)
    return out


def _large(fx):
    """FxPerf's FxLarge (texts, top-level genies, 1,161 animation points)."""
    return fx.page("FxLarge", "FxPerf")


def _open(win, paths):
    for p in paths:
        win.open_document(p)
    _pump()


def _names(win):
    return [os.path.splitext(os.path.basename(e["doc"].path))[0] for e in win._docs]


def _shown(win):
    return os.path.splitext(os.path.basename(win.doc.path))[0] if win.doc else None


# ------------------------------------------------------------------- the Settings window
def _dialog(win, page=""):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win, page)
    dlg.show()
    _pump()
    return dlg


def _wait_live(cond=None, ms=3000):
    QtTest.QTest.qWait(200)
    waited = 200
    while cond is not None and not cond() and waited < ms:
        QtTest.QTest.qWait(50)
        waited += 50


def _set(dlg, key, value, cond=None):
    """Change the row of ``key`` as a user does and wait for the live apply."""
    w = dlg._row_widgets[key]
    if isinstance(w, QtWidgets.QCheckBox):
        w.setChecked(bool(value))
    elif isinstance(w, QtWidgets.QComboBox):
        k = w.findData(value)
        assert k >= 0, (key, value)
        w.setCurrentIndex(k)
    elif isinstance(w, QtWidgets.QSpinBox):
        w.setValue(int(value))
    elif isinstance(w, QtWidgets.QLineEdit):
        w.setText(value)
    else:
        raise AssertionError("not a plain row: %s" % key)
    from ctgedit.ui.prefs.settings import app_settings

    _wait_live(cond or (lambda: str(app_settings().value(key)).lower() == str(value).lower()))


def _row(dlg, key):
    return next(r for r in dlg._tabs.rows() if r.key == key)


# ------------------------------------------------------------------- the declarations
def test_every_new_setting_is_declared_with_todays_default_and_has_one_row(win):
    from ctgedit.ui.panels import doc_tabs
    from ctgedit.ui.prefs import window_options as WO
    from ctgedit.ui.prefs.settings import OPTION_DEFAULTS, SETTINGS_KEYS, key_group

    assert SETTINGS_KEYS["ui/new_tab_place"] == "end"
    assert SETTINGS_KEYS["ui/after_close"] == "next"
    assert SETTINGS_KEYS["ui/ask_unpin"] is True
    assert SETTINGS_KEYS["ui/tab_width"] == "normal"
    assert SETTINGS_KEYS["ui/context_tabs_open"] is False
    assert SETTINGS_KEYS["ui/qat"] == ["act_new", "act_open", "act_save", "act_undo", "act_redo"]
    assert SETTINGS_KEYS["ui/menu_quick_row"] == [
        "align.left",
        "align.hcentre",
        "align.right",
        "act_group",
        "act_front",
        "act_back",
    ]
    assert SETTINGS_KEYS["ui/hud_buttons"] == [
        "act_obj_props",
        "act_group",
        "act_front",
        "act_back",
        "act_del",
    ]
    assert SETTINGS_KEYS["ui/palette_keep_search"] is False
    assert SETTINGS_KEYS["ui/quick_open_scope"] == "project"
    assert SETTINGS_KEYS["opt/new_page_name"] == "NewPage"
    assert SETTINGS_KEYS["opt/compile_results"] == "errors"
    assert SETTINGS_KEYS["opt/an_warn"] == 3000 == win.AN_ADVICE_LIMIT
    assert OPTION_DEFAULTS["single_instance"] is True
    assert OPTION_DEFAULTS["explorer_session"] is False
    # the module values at their defaults: the tab bar's 12 characters and 240 px
    assert (doc_tabs.MIN_CHARS, doc_tabs.MAX_TAB_WIDTH) == (12, 240)
    assert WO.CONTEXT_TABS_OPEN is False and WO.AN_WARN == 3000
    for k in ("ui/qat", "ui/menu_quick_row", "ui/hud_buttons"):
        assert key_group(k) == "Ribbon and Quick Access"
    # the rows offer the choices the code reads (one table each)
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    spec = {r.key: r for r in SettingsDialog.ROWS}
    for key, table in (
        ("ui/new_tab_place", WO.NEW_TAB_PLACE),
        ("ui/after_close", WO.AFTER_CLOSE),
        ("ui/tab_width", WO.TAB_WIDTHS),
        ("ui/quick_open_scope", WO.QUICK_OPEN_SCOPES),
        ("opt/compile_results", WO.COMPILE_RESULTS),
    ):
        assert tuple(spec[key].choices) == table, key
    assert spec["opt/an_warn"].span == WO.AN_WARN_RANGE
    dlg = _dialog(win)
    try:
        where = {}
        for p in dlg._tabs.pages:
            for s in p.sections:
                for r in s.rows:
                    if r.key in KEYS:
                        assert r.key not in where, r.key
                        where[r.key] = (p.title, s.title)
                        assert not r.modified, r.key
        assert set(where) == set(KEYS)
        assert where["ui/tab_width"] == ("Appearance", "Document tabs")
        assert where["opt/an_warn"] == ("Appearance", "Status bar")
        assert where["ui/qat"] == ("Ribbon", "Quick Access")
        assert where["opt/compile_results"] == ("Pages and libraries", "Run")
        assert where["opt/single_instance"] == ("Pages and libraries", "Starting and switching pages")
        # (one Document tabs section)
        titles = [s.title for s in dlg._tabs.widget(dlg._tabs.indexOf("Appearance")).sections]
        assert titles.count("Document tabs") == 1
        assert "Default: New…, Open…, Save, Undo, Redo" in _row(dlg, "ui/qat").field.toolTip()
    finally:
        dlg.close_live()


# ------------------------------------------------------------------- Document tabs
def test_open_a_new_tab_at_the_end_or_next_to_the_current_tab(win, fx_install):
    a, b, c, d, e = _copies(fx_install, ["A", "B", "C", "D", "E"])
    _open(win, [a, b, c])
    win.show_tab(0)
    _open(win, [d])  # default: at the end
    assert _names(win) == ["A", "B", "C", "D"] and _shown(win) == "D"
    dlg = _dialog(win)
    _set(dlg, "ui/new_tab_place", "next")
    dlg.close_live()
    win.show_tab(0)
    _open(win, [e])
    assert _names(win) == ["A", "E", "B", "C", "D"] and _shown(win) == "E"
    assert [win.doc_tabs.tabText(i) for i in range(5)] == ["A", "E", "B", "C", "D"]
    # a pinned tab stays first: a page opened from it goes after the pinned tabs
    win.show_tab(0)
    win.act_pin_tab.trigger()
    win.show_tab(1)
    win.act_pin_tab.trigger()  # A and E pinned
    win.show_tab(0)
    (f,) = _copies(fx_install, ["F"])
    _open(win, [f])
    assert _names(win) == ["A", "E", "F", "B", "C", "D"]


def test_after_closing_a_tab_its_neighbour_or_the_page_used_before(win, fx_install):
    a, b, c, d = _copies(fx_install, ["A", "B", "C", "D"])
    _open(win, [a, b, c, d])
    win.show_tab(3)  # D, then B
    win.show_tab(1)
    win.act_close_doc.trigger()  # default: the neighbour
    _pump()
    assert _names(win) == ["A", "C", "D"] and _shown(win) == "C"
    dlg = _dialog(win)
    _set(dlg, "ui/after_close", "last")
    dlg.close_live()
    win.show_tab(2)  # D, then A
    win.show_tab(0)
    win.act_close_doc.trigger()  # the page used before: D
    _pump()
    assert _names(win) == ["C", "D"] and _shown(win) == "D"
    # (the page used before, whatever order Ctrl+Tab goes in: the tip and the manual do not
    # tie it to Ctrl+Tab)
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    tip = next(r.tip for r in SettingsDialog.ROWS if r.key == "ui/after_close")
    assert "Ctrl+Tab" not in tip and "the page you had open just before" in tip
    import ctgedit.help as H

    md = open(os.path.join(os.path.dirname(H.__file__), "pages", "settings.md"), encoding="utf-8").read()
    para = md[md.index("**After closing a tab, show**") :][:200]
    assert "Ctrl+Tab" not in para.split(";")[0]


def test_ask_before_closing_a_pinned_tab(win, fx_install, monkeypatch):
    a, b = _copies(fx_install, ["A", "B"])
    _open(win, [a, b])
    asked = []
    monkeypatch.setattr(type(win), "_ask_unpin", lambda self, name: asked.append(name) or False)
    win.show_tab(1)
    win.act_pin_tab.trigger()  # B pinned (it moves first)
    assert _names(win)[0] == "B" and win._docs[0].get("pinned")
    win.show_tab(0)
    win.act_close_doc.trigger()  # default: asked, No keeps it
    assert asked == ["B"] and _names(win) == ["B", "A"]
    dlg = _dialog(win)
    _set(
        dlg,
        "ui/ask_unpin",
        False,
        cond=lambda: not __import__("ctgedit.ui.prefs.window_options", fromlist=["x"]).ask_unpin(),
    )
    dlg.close_live()
    win.show_tab(0)
    win.act_close_doc.trigger()  # closes at once
    _pump()
    assert asked == ["B"] and _names(win) == ["A"]


def test_tab_width_narrow_normal_wide(win, fx_install):
    from ctgedit.ui.panels import doc_tabs

    long_name = "Tank_farm_overview_with_a_long_name_for_tabs"
    (p,) = _copies(fx_install, [long_name])
    _open(win, [p])
    win.show()
    _pump()
    bar = win.doc_tabs
    k = bar.currentIndex()

    def sizes():
        _pump()
        return bar.tabSizeHint(k).width(), bar.minimumTabSizeHint(k).width()

    normal = sizes()
    assert normal[0] <= 240 + 1
    dlg = _dialog(win)
    _set(dlg, "ui/tab_width", "narrow", cond=lambda: doc_tabs.MAX_TAB_WIDTH == 160)
    narrow = sizes()
    _set(dlg, "ui/tab_width", "wide", cond=lambda: doc_tabs.MAX_TAB_WIDTH == 360)
    wide = sizes()
    dlg.close_live()
    assert narrow[0] <= 160 + 1 < normal[0] < wide[0] <= 360 + 1
    assert (doc_tabs.MIN_CHARS, doc_tabs.MAX_TAB_WIDTH) == (16, 360)


# ------------------------------------------------------------------- Ribbon
def test_a_canvas_selection_opens_its_contextual_tab_or_only_tints_it(win, fx_install):
    from ctgedit.model.document import ObjectKind

    m = _large(fx_install)
    _open(win, [m])
    win.show()
    rb = win.ribbon
    text_i = next(i for i, o in enumerate(win.doc.page.objects) if o.kind is ObjectKind.TEXT)

    def select(indices):
        rb._tabs.setCurrentIndex(0)  # the Design tab shown
        win.view.selectionChanged.emit(indices)  # (what a click on the canvas sends)
        _pump()
        return [rb._tabs.tabText(i) for i in range(rb._tabs.count())], rb._tabs.tabText(
            rb._tabs.currentIndex()
        )

    titles, cur = select([text_i])
    assert "Text" in titles and cur == "Design"  # default: only tinted, Design's tools stay
    select([])
    dlg = _dialog(win)
    _set(
        dlg,
        "ui/context_tabs_open",
        True,
        cond=lambda: __import__("ctgedit.ui.prefs.window_options", fromlist=["x"]).CONTEXT_TABS_OPEN,
    )
    dlg.close_live()
    titles, cur = select([text_i])
    assert "Text" in titles and cur == "Text"  # opened


def _list_field(dlg, key):
    from ctgedit.ui.prefs.command_list import CommandListField

    f = dlg._row_widgets[key]
    assert isinstance(f, CommandListField)
    return f


def _add_by_name(f, cid, monkeypatch):
    """Add… as the user does: the command chosen by its name in the list that opens."""
    label = next(lab for lab, i in f.choices() if i == cid)
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: (label, True)))
    f.add_button.click()


def _remove(f, cid):
    row = f.value().index(cid)
    f.list.setCurrentRow(row)
    f.remove_button.click()


def test_quick_access_commands(win, monkeypatch):
    rb = win.ribbon
    assert rb.qat_actions() == [win.act_new, win.act_open, win.act_save, win.act_undo, win.act_redo]
    names = [b.objectName() for b in rb._qat_buttons]
    assert names[3] == "QatArrow"  # Save keeps its arrow
    dlg = _dialog(win)
    f = _list_field(dlg, "ui/qat")
    assert f.value() == ["act_new", "act_open", "act_save", "act_undo", "act_redo"]
    _remove(f, "act_open")
    _add_by_name(f, "act_copy", monkeypatch)
    f.list.setCurrentRow(f.value().index("act_copy"))
    f.up_button.click()  # Copy before Redo
    from ctgedit.ui.prefs.settings import app_settings

    _wait_live(lambda: rb.qat_actions()[-2:] == [win.act_copy, win.act_redo])
    assert rb.qat_actions() == [win.act_new, win.act_save, win.act_undo, win.act_copy, win.act_redo]
    assert app_settings().value("ui/qat", type=list) == [
        "act_new",
        "act_save",
        "act_undo",
        "act_copy",
        "act_redo",
    ]
    assert [b.objectName() for b in rb._qat_buttons][2] == "QatArrow"
    # the button runs its command
    ran = []
    win.act_copy.setEnabled(True)
    win.act_copy.triggered.connect(lambda *_a: ran.append(1))
    btn = next(b for b in rb._qat_buttons if b.defaultAction() is win.act_copy)
    btn.click()
    assert ran == [1]
    # Undo Changes puts the five back
    dlg.undo_changes()
    _pump()
    assert rb.qat_actions() == [win.act_new, win.act_open, win.act_save, win.act_undo, win.act_redo]
    dlg.close_live()
    # a new window builds its row from the setting too
    app_settings().setValue("ui/qat", ["act_redo"])
    from ctgedit.ui.main_window import MainWindow

    w2 = MainWindow()
    assert w2.ribbon.qat_actions() == [w2.act_redo]


def test_quick_row_of_the_right_click_menu(win, fx_install, monkeypatch):
    from ctgedit.ui.window import context_menu

    (a,) = _copies(fx_install, ["A"])
    _open(win, [a])
    root = win.doc.page.roots()[-1]
    win.view.selectionChanged.emit([root])
    _pump()

    def quick_row():
        menu = context_menu.build_canvas_menu(win, QtCore.QPoint(10, 10), [root])
        first = menu.actions()[0]
        if not isinstance(first, QtWidgets.QWidgetAction):
            return None
        return [b.defaultAction() for b in first.defaultWidget().findChildren(QtWidgets.QToolButton)]

    assert quick_row() == list(win.align_actions[:3]) + [win.act_group, win.act_front, win.act_back]
    dlg = _dialog(win)
    f = _list_field(dlg, "ui/menu_quick_row")
    for cid in list(f.value()):
        _remove(f, cid)
    from ctgedit.ui.prefs.settings import app_settings

    _wait_live(lambda: app_settings().value("ui/menu_quick_row", None, type=list) == [])
    assert quick_row() is None  # empty: no row
    _add_by_name(f, "act_copy", monkeypatch)
    _wait_live(lambda: app_settings().value("ui/menu_quick_row", None, type=list) == ["act_copy"])
    dlg.close_live()
    assert quick_row() == [win.act_copy]


def test_selection_toolbar_commands(win, fx_install, monkeypatch):
    m = _large(fx_install)
    _open(win, [m])
    win.show()
    hud = win.hud
    assert hud.actions_shown() == [win.act_obj_props, win.act_group, win.act_front, win.act_back, win.act_del]
    dlg = _dialog(win)
    f = _list_field(dlg, "ui/hud_buttons")
    _remove(f, "act_group")
    f.list.setCurrentRow(f.value().index("act_del"))
    for _ in range(4):
        f.up_button.click()  # Delete first
    _wait_live(lambda: hud.actions_shown()[:1] == [win.act_del])
    dlg.close_live()
    assert hud.actions_shown() == [win.act_del, win.act_obj_props, win.act_front, win.act_back]
    # the Properties button still becomes Genie Parameters on a genie
    g = next(i for i, o in enumerate(win.doc.page.objects) if o.parent is None and o.is_genie)
    win._on_canvas_selection([g])
    win._show_hud()
    assert hud.isVisible()
    assert hud.actions_shown() == [win.act_del, win.act_genie_params, win.act_front, win.act_back]
    # no commands: no bar
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs import settings_pages

    app_settings().setValue("ui/hud_buttons", [])
    settings_pages.run_apply_hooks(["ui/hud_buttons"])
    win._show_hud()
    assert hud.actions_shown() == [] and not hud.isVisible()


def test_the_command_palette_opens_with_the_last_search(win):
    win.show()
    pal = win.palette_popup
    win.act_palette.trigger()
    _pump()
    QtTest.QTest.keyClicks(pal._edit, "grid")
    pal.hide()
    win.act_palette.trigger()  # default: empty
    _pump()
    assert pal._edit.text() == ""
    QtTest.QTest.keyClicks(pal._edit, "grid")
    pal.hide()
    dlg = _dialog(win)
    _set(dlg, "ui/palette_keep_search", True)
    dlg.close_live()
    win.act_palette.trigger()
    _pump()
    assert pal._edit.text() == "grid" and pal._edit.selectedText() == "grid"
    QtTest.QTest.keyClicks(pal._edit, "snap")  # typing replaces it
    assert pal._edit.text() == "snap"
    pal.hide()


def test_quick_open_searches_first(win):
    win.show()
    win.act_quick_open.trigger()
    pop = win.quick_open_popup
    assert pop.scope == "project"  # default: this project
    pop.hide()
    dlg = _dialog(win)
    _set(dlg, "ui/quick_open_scope", "install", cond=lambda: pop.scope == "install")
    assert pop.scope == "install"  # (the open popup follows)
    win.act_quick_open.trigger()
    assert pop.scope == "install"
    pop._scope_btn.click()  # Tab's button: back to this project
    assert pop.scope == "project"
    pop.hide()
    _set(dlg, "ui/quick_open_scope", "last")
    dlg.close_live()
    win.act_quick_open.trigger()
    pop._scope_btn.click()  # all projects, used last
    pop.hide()
    from ctgedit.ui.main_window import MainWindow

    w2 = MainWindow()
    w2.show()
    w2.act_quick_open.trigger()
    assert w2.quick_open_popup.scope == "install"
    w2.quick_open_popup.hide()


# ------------------------------------------------------------------- Pages and libraries
def test_name_new_pages(win, fx_install, monkeypatch):
    from ctgedit.ui.dialog import quick_open as QO
    from ctgedit.ui.prefs.settings import app_settings

    (a,) = _copies(fx_install, ["A"])
    _open(win, [a])
    app_settings().setValue("pagebrowser/new/template", "fx_style|normal|1024x768|0")
    win._update_actions()
    made = []
    monkeypatch.setattr(
        type(win), "_create_page", lambda self, ref, linked, path: made.append(os.path.basename(path))
    )
    win.act_new_from_last.trigger()  # default: NewPage
    assert made == ["NewPage.ctg"]
    dlg = _dialog(win)
    _set(dlg, "opt/new_page_name", "Unit")
    dlg.close_live()
    win.act_new_from_last.trigger()
    shutil.copyfile(a, os.path.join(os.path.dirname(a), "Unit.ctg"))
    win.act_new_from_last.trigger()  # taken: the next free one
    QO.make_page_from_template(win, "fx_style", "normal@XGA")  # Quick Open's template row
    assert made[1:] == ["Unit.ctg", "Unit_2.ctg", "Unit_2.ctg"]
    app_settings().setValue("opt/new_page_name", "a:b")  # not a page name
    win.act_new_from_last.trigger()
    assert made[-1] == "NewPage.ctg"


def test_after_compile_show_the_results(win, fx_install, monkeypatch):
    from ctgedit.backend import compiler

    m = _large(fx_install)
    _open(win, [m])
    win.doc.dirty = False
    warn = types.SimpleNamespace(
        severity="warning",
        code="W1",
        project="FxPerf",
        file="FxLarge",
        line=1,
        message="Something",
        context="",
    )
    res = types.SimpleNamespace(
        project="FxPerf",
        outcome="completed",
        errors=[],
        warnings=[warn],
        seconds=1.0,
        note="",
        messages=[warn],
    )
    # (never the real compiler; the fake install's page counts as a project of the install)
    monkeypatch.setattr(compiler, "root_project_for", lambda _d: "FxPerf")
    monkeypatch.setattr(compiler, "compile_project", lambda *_a, **_k: res)
    monkeypatch.setattr(type(win), "_outside_install", lambda self, _d, _v: False)
    monkeypatch.setattr(type(win), "_settle_other_unsaved", lambda self, _c: True)
    shown = []
    monkeypatch.setattr(type(win), "_show_compile_results", lambda self, r, page="": shown.append(r))

    def compile_now():
        shown.clear()
        win.act_compile.setEnabled(True)  # (a copy outside the install is not compiled)
        win.act_compile.trigger()
        _pump()
        return bool(shown)

    assert not compile_now()  # default: warnings only do not open it
    res.errors = [warn]
    assert compile_now()  # an error opens it
    res.errors = []
    dlg = _dialog(win)
    _set(dlg, "opt/compile_results", "problems")
    assert compile_now()  # warnings open it now
    res.warnings = []
    assert not compile_now()  # a clean compile does not
    _set(dlg, "opt/compile_results", "always")
    dlg.close_live()
    assert compile_now()  # always
    # the command hands the setting on; Run keeps its own (only a failed compile)
    calls = []
    monkeypatch.setattr(type(win), "compile_project", lambda self, show="always": calls.append(show))
    win.act_compile.trigger()
    assert calls == ["always"]


def test_open_pages_in_the_ctgedit_already_running(win, monkeypatch):
    from ctgedit import app as A

    monkeypatch.delenv("CTG_NO_SINGLE_INSTANCE", raising=False)
    assert A.hands_over(A.parse_args(["ctgedit", "x.ctg"]))  # default: hand over
    assert not A.hands_over(A.parse_args(["ctgedit", "--new-instance"]))
    dlg = _dialog(win)
    _set(dlg, "opt/single_instance", False)
    dlg.close_live()
    assert not A.hands_over(A.parse_args(["ctgedit", "x.ctg"]))


def test_a_page_opened_from_explorer_also_reopens_the_last_pages(win, fx_install):
    from ctgedit import app as A
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    a, b = _copies(fx_install, ["A", "B"])
    st.setValue("session/open_files", [a])
    st.setValue("session/last_file", a)
    MainWindow._session_seen.clear()
    w = A.start_window(["ctgedit", b])  # default: only the page named
    assert _names(w) == ["B"]
    _close_all(keep=win)
    dlg = _dialog(win)
    _set(dlg, "opt/explorer_session", True)
    dlg.close_live()
    st.setValue("session/open_files", [a])
    st.setValue("session/last_file", a)
    MainWindow._session_seen.clear()
    w = A.start_window(["ctgedit", b])
    assert _names(w) == ["A", "B"] and _shown(w) == "B"
    _close_all(keep=win)
    # the editor's own start (the window first, its pages on the next turn) too
    st.setValue("session/open_files", [a])
    st.setValue("session/last_file", a)
    MainWindow._session_seen.clear()
    w = A.start_window(["ctgedit", b], defer=True)
    _wait_live(lambda: len(w._docs) == 2 and _shown(w) == "B")
    assert _names(w) == ["A", "B"] and _shown(w) == "B"
    _close_all(keep=win)
    # with On start: Open nothing the page alone opens
    st.setValue("opt/startup", "none")
    MainWindow._session_seen.clear()
    w = A.start_window(["ctgedit", b])
    assert _names(w) == ["B"]


# ------------------------------------------------------------------- Appearance
def test_warn_about_pages_with_more_animation_points_than(win, fx_install):
    _open(win, [_large(fx_install)])
    win._update_an_count()
    n = win._an_count
    assert 500 < n <= 3000, n  # (FxLarge: 1,161)
    lbl = win.lbl_ans
    assert not lbl.text().startswith("⚠")  # default: under 3000
    dlg = _dialog(win)
    spin = dlg._row_widgets["opt/an_warn"]
    assert spin.singleStep() == 500 and (spin.minimum(), spin.maximum()) == (500, 20000)
    _set(dlg, "opt/an_warn", 500, cond=lambda: lbl.text().startswith("⚠"))
    dlg.close_live()
    assert lbl.text() == "⚠ %d animation points" % n
    assert "500" in lbl.toolTip() and "3000" in lbl.toolTip()


def test_a_longer_quick_access_row_fits_the_header_again(win):
    """A Quick Access row changed in Settings fits the header's Search field and utility
    buttons again at once."""
    from ctgedit.ui.prefs import keymap, settings_pages
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS, app_settings

    win.resize(1150, 860)
    win.show()
    _pump()
    hs = win.header_search
    hs.fit()
    _pump()
    named = hs.companions_named()
    many = list(SETTINGS_KEYS["ui/qat"])
    for c in keymap.commands(win):
        if len(many) >= 18:
            break
        if c.id.startswith("act_") and c.id not in many and not c.action.icon().isNull():
            many.append(c.id)
    app_settings().setValue("ui/qat", many)
    settings_pages.run_apply_hooks(["ui/qat"])
    _wait_live(lambda: len(win.ribbon.qat_actions()) == len(many))
    _pump()
    live = (hs.state, hs.companions_named())
    hs.fit()
    assert (hs.state, hs.companions_named()) == live
    assert named and not live[1]  # the buttons dropped their names
    # and back: the five again give the names back
    app_settings().remove("ui/qat")
    settings_pages.run_apply_hooks(["ui/qat"])
    _wait_live(lambda: len(win.ribbon.qat_actions()) == 5)
    _pump()
    assert hs.companions_named()


def test_the_grid_sizes_stay_in_the_command_lists_after_a_restart(win):
    """The grid sizes (View > Grid) are named before the Quick Access row and the selection
    toolbar look their ids up, so a new window keeps them; Add… says what they are."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    st.setValue("ui/qat", ["act_new", "grid.size.8"])
    st.setValue("ui/hud_buttons", ["grid.size.8", "act_del"])
    w2 = MainWindow()
    assert w2.ribbon.qat_actions() == [w2.act_new, w2._grid_actions[8]]
    assert w2.hud.actions_shown() == [w2._grid_actions[8], w2.act_del]
    dlg = _dialog(w2)
    try:
        f = _list_field(dlg, "ui/qat")
        assert f.value() == ["act_new", "grid.size.8"]
        assert f.list.item(1).text() == "8 px (View > Grid)"
        labels = {i: lab for lab, i in _list_field(dlg, "ui/menu_quick_row").choices()}
        assert labels["grid.size.10"] == "10 px (View > Grid)"
        assert labels["grid.size.1"] == "1 px (View > Grid)"
        assert labels["act_copy"] == "Copy"  # a plain name stays plain
    finally:
        dlg.close_live()


def test_a_garbled_command_list_reads_as_its_default(win):
    from ctgedit.ui.prefs import window_options as WO
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS, app_settings

    st = app_settings()
    st.setValue("ui/qat", "garbage")
    st.setValue("ui/hud_buttons", "12345")
    st.setValue("ui/menu_quick_row", ["nothing", "at all"])
    assert WO.quick_access_actions(win) == [
        win.act_new,
        win.act_open,
        win.act_save,
        win.act_undo,
        win.act_redo,
    ]
    assert WO.hud_actions(win) == [win.act_obj_props, win.act_group, win.act_front, win.act_back, win.act_del]
    assert WO.menu_quick_actions(win) == list(win.align_actions[:3]) + [
        win.act_group,
        win.act_front,
        win.act_back,
    ]
    # the Settings window shows the default, not the junk
    dlg = _dialog(win)
    try:
        for k in WO.LIST_KEYS:
            assert _list_field(dlg, k).value() == SETTINGS_KEYS[k], k
            assert not _row(dlg, k).modified, k
    finally:
        dlg.close_live()
    # an empty list is still no row; one command among junk is kept
    st.setValue("ui/menu_quick_row", [])
    assert WO.menu_quick_actions(win) == []
    st.setValue("ui/qat", ["garbage", "act_redo"])
    assert WO.quick_access_actions(win) == [win.act_redo]


def test_a_garbled_on_off_setting_reads_as_its_default(win, monkeypatch):
    from ctgedit import app as A
    from ctgedit.ui.prefs import window_options as WO
    from ctgedit.ui.prefs.settings import app_settings

    monkeypatch.delenv("CTG_NO_SINGLE_INSTANCE", raising=False)
    st = app_settings()
    for junk in ("garbage", "", "2"):
        for k in WO.FLAG_KEYS:
            st.setValue(k, junk)
        assert WO.ask_unpin() is True and WO.palette_keep_search() is False, junk
        assert WO.single_instance() is True and WO.explorer_session() is False, junk
        WO.load()
        assert WO.CONTEXT_TABS_OPEN is False, junk
        assert A.hands_over(A.parse_args(["ctgedit", "x.ctg"])), junk
    for raw, want in (
        ("true", True),
        ("1", True),
        ("false", False),
        ("0", False),
        (True, True),
        (False, False),
        ("True", True),
    ):
        st.setValue("opt/explorer_session", raw)
        st.setValue("ui/ask_unpin", raw)
        assert WO.explorer_session() is want and WO.ask_unpin() is want, raw
    # the Settings window shows the defaults for the junk
    for k in WO.FLAG_KEYS:
        st.setValue(k, "garbage")
    dlg = _dialog(win)
    try:
        for k in WO.FLAG_KEYS:
            assert dlg._row_widgets[k].isChecked() is WO.flag(k), k
            assert not _row(dlg, k).modified, k
    finally:
        dlg.close_live()


def test_undo_last_change_puts_back_only_the_latest_setting(win):
    """Live settings: Undo Last Change goes back one change."""
    from ctgedit.ui.prefs.settings import app_settings

    dlg = _dialog(win)
    try:
        assert not dlg._btn_undo_last.isEnabled()
        _set(dlg, "ui/context_tabs_open", True)
        _set(dlg, "opt/explorer_session", True)
        assert dlg._btn_undo_last.isEnabled()
        dlg._btn_undo_last.click()
        st = app_settings()
        assert not st.value("opt/explorer_session", False, type=bool)  # (the latest: back)
        assert st.value("ui/context_tabs_open", False, type=bool)  # (the one before: kept)
        dlg._btn_undo_last.click()
        assert not st.value("ui/context_tabs_open", False, type=bool)
        assert not dlg._btn_undo_last.isEnabled()
    finally:
        dlg.close_live()
