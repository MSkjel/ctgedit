"""One Save Changes list for several unsaved pages."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the page copied as A, B, C: FxMain's FxShapes in the fake install (one of each primitive,
#: no library); the copies go into a folder named FOLDER, which the Project column shows
PAGE = "FxShapes"
FOLDER = "FxCopy"


@pytest.fixture()
def win(monkeypatch, fx_install):
    _app()
    monkeypatch.setenv("CTG_LIB_PATH", fx_install.project("FxMain"))
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    for k in (
        "auto_placeholder_missing",
        "auto_update_genies",
        "auto_update_symbols",
        "auto_update_template",
    ):
        st.setValue("opt/" + k, False)
    # (no Graphics Builder question before a save)
    monkeypatch.setattr(MainWindow, "_confirm_builder_save", lambda self, *a, **k: True)
    for name in ("warning", "information"):
        monkeypatch.setattr(
            QtWidgets.QMessageBox, name, staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
        )
    w = MainWindow()
    yield w
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)


def _pages(tmp_path, names):
    import fxinstall

    small = os.path.join(fxinstall.FX_INSTALL, "User", "FxMain", PAGE + ".ctg")
    d = tmp_path / FOLDER
    d.mkdir(exist_ok=True)
    out = []
    for n in names:
        p = d / (n + ".ctg")
        shutil.copyfile(small, p)
        out.append(str(p))
    return out


def _edit(doc):
    r = doc.page.roots()[-1]
    doc.move_objects(doc.subtree(r), 3, 0)


def _open_edited(win, paths):
    docs = []
    for p in paths:
        assert win.open_page(p)
        _edit(win.doc)
        docs.append(win.doc)
    return docs


class _Answer:
    """Stands in for the Save Changes list: records its rows, unticks some, answers."""

    def __init__(self, monkeypatch, choice, untick=()):
        from ctgedit.ui.main_window import MainWindow

        self.rows, self.ticked, self.choice, self.untick = [], [], choice, set(untick)
        monkeypatch.setattr(MainWindow, "_exec_save_changes", staticmethod(self))

    def __call__(self, dlg):
        t = dlg.table
        self.rows = [tuple(t.item(r, c).text() for c in range(t.columnCount())) for r in range(t.rowCount())]
        from PySide6 import QtCore

        self.ticked = [t.item(r, 0).checkState() == QtCore.Qt.Checked for r in range(t.rowCount())]
        for r in range(t.rowCount()):
            if t.item(r, 0).text() in self.untick:
                dlg.set_ticked(r, False)
        self.title = dlg.windowTitle()
        return self.choice


def test_three_unsaved_pages_one_list_save_selected_saves_the_ticked_ones(win, tmp_path, monkeypatch):
    from ctgedit.ui.dialog.save_changes import SaveChangesDialog

    a, b, c = _pages(tmp_path, ["A", "B", "C"])
    before = {p: open(p, "rb").read() for p in (a, b, c)}
    docs = _open_edited(win, [a, b, c])
    ans = _Answer(monkeypatch, SaveChangesDialog.SAVE, untick={"B"})
    asked = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", lambda *x, **k: asked.append(x) or QtWidgets.QMessageBox.Save
    )
    from ctgedit.ui.recovery import AutosaveController

    forgot = []
    monkeypatch.setattr(AutosaveController, "forget", staticmethod(forgot.append))
    ev = QtGui.QCloseEvent()
    win.closeEvent(ev)
    assert ev.isAccepted()  # the window closes
    assert not asked  # (no one-page question)
    assert ans.title == "Unsaved Changes - ctgedit"
    assert [r[0] for r in ans.rows] == ["A", "B", "C"] and ans.ticked == [True] * 3
    assert all(r[1] == FOLDER for r in ans.rows) and ans.rows[0][2] == "edited"
    assert not docs[0].dirty and not docs[2].dirty  # saved
    assert open(a, "rb").read() != before[a] and open(c, "rb").read() != before[c]
    assert open(b, "rb").read() == before[b]  # not saved
    assert docs[1] in forgot  # its recovery copy goes


def test_one_unsaved_page_keeps_the_one_page_question(win, tmp_path, monkeypatch):
    from ctgedit.ui.dialog.save_changes import SaveChangesDialog

    a, b = _pages(tmp_path, ["A", "B"])
    assert win.open_page(a)
    _open_edited(win, [b])
    ans = _Answer(monkeypatch, SaveChangesDialog.CANCEL)
    asked = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", lambda *x, **k: asked.append(x[2]) or QtWidgets.QMessageBox.Discard
    )
    ev = QtGui.QCloseEvent()
    win.closeEvent(ev)
    assert ev.isAccepted() and not ans.rows
    assert asked == ["Save changes to B before closing?"]


def test_cancel_closes_nothing_and_dont_save_closes_all(win, tmp_path, monkeypatch):
    from ctgedit.ui.dialog.save_changes import SaveChangesDialog

    a, b, c = _pages(tmp_path, ["A", "B", "C"])
    before = {p: open(p, "rb").read() for p in (a, b, c)}
    _open_edited(win, [a, b])
    assert win.open_page(c)
    _Answer(monkeypatch, SaveChangesDialog.CANCEL)
    assert not win.close_all_tabs()
    assert len(win._docs) == 3
    ev = QtGui.QCloseEvent()
    win.closeEvent(ev)
    assert not ev.isAccepted()
    _Answer(monkeypatch, SaveChangesDialog.DISCARD)
    assert win.close_all_tabs()
    assert not win._docs and win.doc is None
    assert all(open(p, "rb").read() == before[p] for p in (a, b, c))


def test_a_page_changed_only_automatically_starts_unticked(win, tmp_path, monkeypatch):
    from ctgedit.ui.dialog.save_changes import SaveChangesDialog

    a, b = _pages(tmp_path, ["A", "B"])
    docs = _open_edited(win, [a, b])
    docs[0].ui.auto_changes = [("template re-applied", docs[0]._undo[-1])]
    ans = _Answer(monkeypatch, SaveChangesDialog.CANCEL)
    win.close_all_tabs()
    assert ans.ticked == [False, True]
    assert ans.rows[0][2] == "updated automatically"


def test_save_selected_stops_at_the_first_failure(win, tmp_path, monkeypatch):
    from ctgedit.ui.dialog.save_changes import SaveChangesDialog

    a, b, c = _pages(tmp_path, ["A", "B", "C"])
    docs = _open_edited(win, [a, b, c])
    _Answer(monkeypatch, SaveChangesDialog.SAVE)
    from ctgedit.ui.main_window import MainWindow

    order = []

    def save(self):
        order.append(os.path.basename(self.doc.path))
        if self.doc is docs[1]:
            return False
        self.doc.dirty = False
        return True

    monkeypatch.setattr(MainWindow, "save", save)
    ev = QtGui.QCloseEvent()
    win.closeEvent(ev)
    assert not ev.isAccepted()  # the window stays
    assert order == ["A.ctg", "B.ctg"]  # stopped at B, C not tried
    assert docs[2].dirty and len(win._docs) == 3
    assert "B" in win.statusBar().currentMessage()


def test_the_dialog_itself():
    _app()
    from ctgedit.ui.dialog.save_changes import SaveChangesDialog
    from ctgedit.ui.help import dialog_topic

    rows = [
        ("k1", "FxShapes", "FxMain", "4 steps", r"C:\P\FxMain", True),
        ("k2", "FxAnim", "FxMain", "updated automatically", r"C:\P\FxMain", False),
    ]
    dlg = SaveChangesDialog(rows)
    assert dlg.selected() == ["k1"]
    dlg.set_ticked(1, True)
    assert dlg.selected() == ["k1", "k2"]
    assert dlg.btn_save.isDefault() and dlg.btn_save.text() == "Save All"
    assert dlg.btn_discard.text() == "Don't Save"
    assert [dlg.table.horizontalHeaderItem(c).text() for c in range(4)] == [
        "Page",
        "Project",
        "Changes",
        "Folder",
    ]
    assert dialog_topic(dlg) == "opening-and-saving#unsaved-changes"
    assert SaveChangesDialog.RESIZE == "both"
    # one line a row: a long folder loses its middle
    assert not dlg.table.wordWrap()
    assert dlg.table.textElideMode() == QtCore.Qt.ElideMiddle
    dlg.btn_discard.click()
    assert dlg.choice == SaveChangesDialog.DISCARD


def test_save_selected_needs_a_ticked_page():
    """With no page ticked, Save Selected is off and Enter is Cancel: it never closes the
    pages unsaved (only Don't Save does), and the outcome is said in words."""
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    from ctgedit.ui.dialog.save_changes import SaveChangesDialog

    _app()
    rows = [
        ("A", "FxParts", "FxMain", "1 change", "C:/x", True),
        ("B", "FxGenies", "FxMain", "1 change", "C:/x", True),
    ]
    dlg = SaveChangesDialog(rows)
    assert dlg.btn_save.isEnabled() and dlg.btn_save.isDefault()
    assert dlg.outcome.text() == "Save 2 pages."
    dlg.set_ticked(1, False)
    assert dlg.outcome.text() == "Save 1 page and discard the changes to 1 page."
    # (the button says it discards too, and Enter never discards: Cancel is the default)
    assert dlg.btn_save.isEnabled() and dlg.btn_save.text() == "Save 1, Discard 1"
    assert not dlg.btn_save.isDefault() and dlg.btn_cancel.isDefault()
    dlg.set_ticked(0, False)
    assert not dlg.btn_save.isEnabled() and dlg.btn_cancel.isDefault()
    assert "No page is ticked" in dlg.outcome.text()
    dlg.show()
    dlg.table.setFocus()
    QTest.keyClick(dlg.table, QtCore.Qt.Key_Return)
    assert dlg.choice == SaveChangesDialog.CANCEL
    dlg.close()
