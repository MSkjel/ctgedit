"""Quick Open search and rows."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the loose folder's pages (the names matter to the scorer): copies of FxMain's FxBig
#: (140 records, AN 1 and up) and FxShapes
FOLDER_PAGES = (
    ("FxMixing_L3", "FxShapes"),
    ("FxMixer", "FxBig"),
    ("FxMix_Lines", "FxShapes"),
    ("FxPump_L3", "FxShapes"),
)


def _last_an(path):
    """The highest AN of a page (FxBig's: the generator numbers them up from 1, so no
    other AN of the page holds its digits in order)."""
    from ctgedit.io.reader import read_page

    return max(o.an for o in read_page(str(path)).objects if o.an)


def _pump(n=4):
    for _ in range(n):
        _app().processEvents()


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1600, 1000)
    w.show()
    _pump()
    yield w
    q = getattr(w, "quick_open_popup", None)
    if q is not None:
        q.hide()
    w.palette_popup.hide()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()


@pytest.fixture()
def folder(tmp_path, fx_install):
    """A loose folder with copies of the fake install's pages (FOLDER_PAGES)."""
    d = tmp_path / "pages"
    d.mkdir()
    for name, src in FOLDER_PAGES:
        shutil.copyfile(fx_install.page(src), d / (name + ".ctg"))
    return d


def _key(widget, key, mods=QtCore.Qt.NoModifier, text=""):
    QtWidgets.QApplication.sendEvent(widget, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods, text))


def test_mxl3_finds_mixing_l3_first(win, folder):
    from ctgedit.ui.dialog.quick_open import QuickOpen

    q = QuickOpen(win)
    q.set_project_dir(str(folder))
    names = [h.name for h in q.hits("mxl3")]
    assert names[0] == "FxMixing_L3"
    # today's order breaks ties: pages first, then by name
    assert [h.name for h in q.hits("mix")][:2] == ["FxMix_Lines", "FxMixer"]
    q.deleteLater()


def test_rows_have_the_kind_icon_the_marked_letters_and_the_kind(win, folder):
    q = _open(win, folder, "mxl3")
    r = q._list.rows()[0]
    assert r.kind == "page" and r.text == "FxMixing_L3"
    assert r.hits == (2, 4, 9, 10)
    assert r.chip == "page" and r.icon is not None and not r.icon.isNull()


def _open(win, folder, text=""):
    win.open_document(str(folder / "FxMixer.ctg"))
    _pump()
    win.quick_open(prefill=text)
    _pump()
    return win.quick_open_popup


def test_nothing_typed_lists_open_tabs_recent_then_the_pages(win, folder):
    win.open_document(str(folder / "FxPump_L3.ctg"))
    win.open_document(str(folder / "FxMixer.ctg"))
    _pump()
    win.quick_open()
    q = win.quick_open_popup
    rows = q._list.rows()
    heads = [r.text for r in rows if r.kind == "header"]
    assert heads[0] == "Open tabs"
    assert rows[1].kind == "tab" and rows[1].text.startswith("FxPump_L3")
    assert "Pages of the project" in heads
    # (the current page is not an open tab to go to; it is still one of the pages)
    assert not any(r.kind == "tab" and r.text.startswith("FxMixer") for r in rows)
    k = heads.index("Pages of the project")
    assert heads[-1] == "Pages of the project" and k >= 1
    # Enter on the open tab shows it
    q._list.setCurrentRow(1)
    _key(q._edit, QtCore.Qt.Key_Return)
    _pump()
    assert os.path.basename(win.doc.path) == "FxPump_L3.ctg"


def test_at_and_colon_find_the_objects_of_the_page(win, folder):
    AN = _last_an(folder / "FxMixer.ctg")
    assert AN > 100
    q = _open(win, folder, "@%d" % AN)
    rows = [r for r in q._list.rows() if r.selectable]
    an = next(i for i, o in enumerate(win.doc.page.objects) if o.an == AN)
    assert rows[0].kind == "object" and rows[0].data.data == an
    _key(q._edit, QtCore.Qt.Key_Return)
    _pump()
    assert win._sel == [an]
    q = _open(win, folder, ":%d" % AN)
    assert [r.kind for r in q._list.rows()] == ["goto"]


def test_gt_hands_the_text_to_the_palette(win, folder):
    q = _open(win, folder)
    q._edit.setText(">grid")
    _pump()
    assert not q.isVisible()
    p = win.palette_popup
    assert p.isVisible() and p._edit.text() == ">grid"
    assert all(r.kind == "command" for r in p._list.rows() if r.selectable)
    p.hide()


def test_tab_switches_the_scope(win, folder):
    q = _open(win, folder)
    assert q.scope == "project" and q._scope_btn.text() == "This project"
    _key(q._edit, QtCore.Qt.Key_Tab)
    assert q.scope == "install" and q._scope_btn.text() == "All projects"
    # every project of the install: a FxMain page and a FxSa page both
    names = {h.name for h in q.hits("p:fxshapes")} | {h.name for h in q.hits("p:fxsablank")}
    assert {"FxShapes", "FxSaBlank"} <= names
    q._scope_btn.click()
    assert q.scope == "project"


def test_the_template_and_super_genie_prefixes():
    from ctgedit.ui.dialog.quick_open import split_query, query_mode

    assert split_query("tpl:normal") == (("template",), "normal")
    assert split_query("sg: pump") == (("supergenie",), "pump")
    assert split_query("s:x") == (("symbol",), "x")
    assert query_mode("@valve") == "objects" and query_mode(":5") == "goto"
    assert query_mode(">grid") == query_mode("?help") == query_mode("set:x") == "palette"


def test_enter_shift_enter_and_ctrl_enter(win, monkeypatch, tmp_path):
    from ctgedit.ui.dialog import quick_open as QO

    entry = type("E", (), {"path": str(tmp_path / "pumps.ctm")})()
    edited, armed, pages, made = [], [], [], []
    monkeypatch.setattr(win, "_open_element", lambda path, el, kind="": edited.append((path, el)))
    monkeypatch.setattr(win, "_arm_symbol_placement", lambda *a: armed.append(a))
    QO.run_hit(win, QO.Hit("genie", "pumps.p1", "", ("pumps", "p1", "ctm"), entry))
    QO.run_hit(win, QO.Hit("genie", "pumps.p1", "", ("pumps", "p1", "ctm"), entry), "edit")
    assert armed == [("pumps", "p1", "ctm")]
    assert edited == [(entry.path, "p1")]

    class _New:
        def open_document(self, path):
            pages.append(path)

    monkeypatch.setattr(win, "_new_window", lambda: _New())
    QO.run_hit(win, QO.Hit("page", "FxShapes", "", r"C:\x\FxShapes.ctg"), "new_window")
    assert pages == [r"C:\x\FxShapes.ctg"]
    # a template: Enter makes a new page from it, Shift+Enter edits it
    monkeypatch.setattr(QO, "make_page_from_template", lambda w, lib, el: made.append((lib, el)))
    QO.run_hit(win, QO.Hit("template", "style.normal", "", ("style", "normal", "ctt"), entry))
    assert made == [("style", "normal")]


def test_a_tag_is_copied_only_with_ctrl_c(win):
    from ctgedit.ui.dialog import quick_open as QO

    cb = QtWidgets.QApplication.clipboard()
    cb.setText("before")
    QO.show_tag(win, "Tank_1_Level")
    assert cb.text() == "before"
    q = QO.QuickOpen(win)
    q._list.set_rows([QO.hit_row(win, QO.Hit("tag", "Tank_1_Level", "", "Tank_1_Level"))])
    _key(q._edit, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)
    assert cb.text() == "Tank_1_Level"
    assert "clipboard" in q._footer.text()
    cb.clear()
    q.deleteLater()


def test_the_preview_shows_the_highlighted_page(win, folder):
    q = _open(win, folder, "mxl3")
    assert q._preview.isVisibleTo(q)
    assert q._pv_name.text() == "FxMixing_L3"
    # a narrow window has no room for it
    q.hide()
    win.resize(800, 700)
    win.quick_open()
    assert not q._preview.isVisibleTo(q)


def test_the_regex_toggle(win, folder):
    q = _open(win, folder)
    q._edit.set_regex(True)
    try:
        q._edit.setText("^FxMi.*L")
        names = [h.name for h in q.hits(q._edit.text())]
        assert names and all(n.startswith("FxMi") and "L" in n for n in names)
    finally:
        q._edit.set_regex(False)


def test_the_frame_takes_the_light_theme_tokens(win):
    from ctgedit.ui.dialog import quick_open as QO
    from ctgedit.ui.style import theme as T

    app = _app()
    q = QO.QuickOpen(win)
    try:
        T.apply_theme(app, "light", 1.0)
        assert T.LIGHT["popup"] in q.styleSheet() and T.LIGHT["border"] in q.styleSheet()
        assert T.DARK["popup"] not in q.styleSheet()
        q.resize(500, 120)
        img = q.grab().toImage()
        # (the frame's background, just inside its edge)
        assert img.pixelColor(20, img.height() - 3).name() == T.LIGHT["popup"]
    finally:
        T.apply_theme(app, "dark", 1.0)
        q.deleteLater()
    assert T.DARK["popup"] in q.styleSheet()
