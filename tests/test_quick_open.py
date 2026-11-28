"""Quick Open."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app


@pytest.fixture
def win(tmp_path):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def test_split_query_prefixes():
    from ctgedit.ui.dialog.quick_open import split_query, KIND_ORDER

    assert split_query("mix") == (tuple(KIND_ORDER), "mix")
    assert split_query("t: Tank") == (("tag",), "Tank")
    assert split_query("F:Play") == (("function",), "Play")
    assert split_query("x:") == (tuple(KIND_ORDER), "x:")


def test_rank_prefers_the_start_then_a_word():
    from ctgedit.ui.dialog.quick_open import _rank

    assert _rank("mix", "Mixer") == 0
    assert _rank("mix", "Tank_Mixer") == 11
    assert 30 <= _rank("xer", "Mixer") < 40
    assert _rank("zz", "Mixer") == -1


def test_quick_open_lists_the_pages_elements_tags_and_functions(win, fx_project):
    from ctgedit.ui.dialog.quick_open import QuickOpen

    q = QuickOpen(win)
    q.set_project_dir(fx_project("FxMain"))
    kinds = {h.kind for h in q.hits("")}
    assert kinds == {"page"}  # nothing typed: the pages
    names = [h.name for h in q.hits("fxshapes")]
    assert names and names[0].lower().startswith("fxshapes")
    assert [h.kind for h in q.hits("p:fxshapes")] == ["page"] * len(q.hits("p:fxshapes"))
    sym = q.hits("s:")
    assert sym and all(h.kind == "symbol" for h in sym)
    lib, name, ext = sym[0].data
    assert ext == "ctl" and sym[0].name == "%s.%s" % (lib, name)
    gen = q.hits("g:")
    assert gen and all(h.kind == "genie" for h in gen) and gen[0].data[2] == "ctm"
    tags = q.hits("t:a")
    assert tags and all(h.kind == "tag" for h in tags)
    assert all("a" in h.name.lower() for h in tags)  # (an equipment item matches by item)
    funcs = q.hits("f:")
    assert funcs and all(h.kind == "function" for h in funcs)
    assert any(h.data.lower().endswith(".ci") and os.path.isfile(h.data) for h in funcs)
    # a mixed search ranks the pages before the elements among equal matches
    mixed = q.hits("a")
    order = [h.kind for h in mixed]
    assert "page" in order and order.index("page") == 0


def test_quick_open_enter_opens_arms_and_shows(win, tmp_path, monkeypatch):
    from ctgedit.ui.dialog.quick_open import QuickOpen, Hit

    q = QuickOpen(win)
    opened = []
    monkeypatch.setattr(win, "open_document", lambda p, **k: opened.append(p))
    page = str(tmp_path / "FxShapes.ctg")  # (a path only: open is monkeypatched)
    q.run_hit(Hit("page", "FxShapes", "", page))
    assert opened == [page]
    armed = []
    monkeypatch.setattr(win, "_arm_symbol_placement", lambda *a: armed.append(a))
    q.run_hit(Hit("symbol", "lib.sym", "", ("lib", "sym", "ctl")))
    assert armed == [("lib", "sym", "ctl")]
    shown = []
    monkeypatch.setattr(win, "show_tag", lambda n: shown.append(n), raising=False)
    q.run_hit(Hit("tag", "FX_LEVEL1", "", "FX_LEVEL1"))
    assert shown == ["FX_LEVEL1"]
    ci = []
    monkeypatch.setattr(win, "open_cicode_file", lambda p: ci.append(p), raising=False)
    q.run_hit(Hit("function", "FxPlay", "", r"C:\x\y.ci"))
    assert ci == [r"C:\x\y.ci"]


def test_quick_open_popup_from_the_window(win, tmp_path, fx_page):
    loose = tmp_path / "loose"
    loose.mkdir()
    dst = loose / "FxShapes.ctg"
    shutil.copyfile(fx_page("FxShapes"), dst)
    win.open_document(str(dst))
    assert win.act_quick_open.isEnabled()
    assert win.act_quick_open.shortcut().toString() == "Ctrl+Shift+O"
    # the menu's and the key's QAction hands its 'checked' (False) to the command: it still opens
    win.act_quick_open.trigger()
    q = win.quick_open_popup
    assert q.isVisible() and q._edit.text() == ""
    q.hide()
    win.quick_open(prefill="fxsh")
    assert q.isVisible()
    # a loose folder lists its own pages only
    assert [h.name for h in q.hits("")] == ["FxShapes"]
    assert q._list.count() >= 1 and q.current_hit().name == "FxShapes"
    q.hide()
    # a tag hit shows the Tags panel with the name as its search (Ctrl+C on the row copies
    # it: tests/test_quick_open_ux.py)
    from ctgedit.ui.dialog import quick_open as QO

    QtWidgets.QApplication.clipboard().setText("before")
    QO.show_tag(win, "FX_SOME_TAG")
    assert win.tags_dock._filter.text() == "FX_SOME_TAG"
    assert QtWidgets.QApplication.clipboard().text() == "before"
    assert not win.dock_tags.isHidden()
