"""Export Page, Isolate Selection and the History panel."""

import os
import sys

import pytest
from PySide6 import QtWidgets
from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


@pytest.fixture
def win(fx_page):
    """A window on FxShapes of the fake install (its copy): one of each primitive, more
    than two roots, all movable."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page("FxShapes"))
    w.resize(1300, 900)
    w.show()
    QtWidgets.QApplication.processEvents()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def test_export_page_writes_png_svg_and_pdf(win, tmp_path, monkeypatch):
    doc = win.doc
    w, h = doc.page.appearance.width, doc.page.appearance.height
    from PySide6 import QtGui

    for ext, pick in ((".png", "200 %"), (".svg", None), (".pdf", None)):
        target = str(tmp_path / ("out" + ext))
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, t=target, **k: (t, ""))
        )
        monkeypatch.setattr(
            QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, p=pick, **k: (p, True))
        )
        win._export_page()
        assert os.path.exists(target) and os.path.getsize(target) > 100, target
        assert "Exported" in win.statusBar().currentMessage()
        if ext == ".png":
            img = QtGui.QImage(target)
            assert (img.width(), img.height()) == (w * 2, h * 2)
        elif ext == ".svg":
            head = open(target, "rb").read(400)
            assert b"<svg" in head and b'viewBox="0 0 %d %d"' % (w, h) in head
        else:
            assert open(target, "rb").read(5) == b"%PDF-"
    # a cancelled file dialog does nothing
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    before = sorted(os.listdir(tmp_path))
    win._export_page()
    assert sorted(os.listdir(tmp_path)) == before
    assert win.act_export_page.isEnabled()


def test_export_page_to_a_missing_folder_says_so_in_every_format(win, tmp_path, monkeypatch):
    """A folder gone before the write: PNG, SVG and PDF each warn, none says Exported."""
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("100 %", True)))
    for ext in (".png", ".svg", ".pdf"):
        target = str(tmp_path / "gone" / ("out" + ext))
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, t=target, **k: (t, ""))
        )
        win.statusBar().clearMessage()
        n = len(warned)
        win._export_page()
        assert len(warned) == n + 1 and "could not be exported" in warned[-1], ext
        assert not os.path.exists(target)
        assert "Exported" not in win.statusBar().currentMessage(), ext


def test_isolate_selection_hides_the_rest_and_toggles_back(win):
    doc = win.doc
    roots = doc.page.roots()
    assert len(roots) > 2
    assert not win.act_isolate.isEnabled()
    win._on_canvas_selection([roots[1]])
    assert win.act_isolate.isEnabled()
    win.isolate_selection()
    assert win._hidden_roots == set(roots) - {roots[1]}
    assert "hidden" in win.statusBar().currentMessage()
    assert win._hidden_chip.isVisible()
    # again: everything back
    win.isolate_selection()
    assert win._hidden_roots == set()
    # Show All after an isolate
    win.isolate_selection()
    assert win._hidden_roots
    win.act_show_all_roots.trigger()
    assert win._hidden_roots == set()


def test_the_history_panel_lists_the_steps_and_goes_to_one(win):
    doc = win.doc
    win.dock_history.show()
    QtWidgets.QApplication.processEvents()
    hd = win.history_dock
    hd.show_document(doc)
    assert hd.list.count() == 1 and hd.list.item(0).text() == "Opened"
    roots = doc.page.roots()
    g = doc.page.objects[roots[0]].geometry
    x0 = g.x
    doc.move_objects([roots[0]], 5, 0)
    doc.move_objects([roots[0]], 7, 0)
    win._update_actions()
    assert hd.list.count() == 3 and hd.list.currentRow() == 2
    assert doc.page.objects[roots[0]].geometry.x == x0 + 12
    # back to the state after the first step
    win.go_to_history(1)
    assert len(doc._undo) == 1 and len(doc._redo) == 1
    assert doc.page.objects[roots[0]].geometry.x == x0 + 5
    assert hd.list.currentRow() == 1 and hd.list.count() == 3
    # forward again
    win.go_to_history(2)
    assert len(doc._undo) == 2 and not doc._redo
    assert doc.page.objects[roots[0]].geometry.x == x0 + 12
    # the opened state
    win.go_to_history(0)
    assert not doc._undo and len(doc._redo) == 2
    assert doc.page.objects[roots[0]].geometry.x == x0
    assert "undone" in win.statusBar().currentMessage()


def test_a_failed_export_leaves_the_file_there_as_it_was(win, tmp_path, monkeypatch):
    target = tmp_path / "out.pdf"
    target.write_bytes(b"%PDF-old")
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    def fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(type(win), "_render_page_to", fail)
    win._export_page()
    assert target.read_bytes() == b"%PDF-old"
    assert not [p.name for p in tmp_path.iterdir() if ".~export" in p.name]
