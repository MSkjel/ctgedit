"""Editing settings."""

import json
import os
import shutil
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from qtkit import app as _app

#: the symbol a test places (library, element, extension)
SYMBOL = ("fxsym", "fx_box", "ctl")
#: the small page the window opens (FxMain)
SMALL_PAGE = "FxMaster"

LEFT = QtCore.Qt.LeftButton
NOMOD = QtCore.Qt.NoModifier


def _pump(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


# ------------------------------------------------------------------------- the window
@pytest.fixture()
def win(fx_install, monkeypatch):
    _app()
    from ctgedit.infra import backups
    from ctgedit.ui.main_window import MainWindow

    # (process-wide values the window sets from Settings: put back after the test)
    monkeypatch.setattr(MainWindow, "MRU_MAX", MainWindow.MRU_MAX)
    monkeypatch.setattr(backups, "KEEP", backups.KEEP)
    monkeypatch.setattr(backups, "MAX_AGE_DAYS", backups.MAX_AGE_DAYS)
    dst = fx_install.page(SMALL_PAGE)
    w = MainWindow()
    w.open_document(str(dst))
    w.resize(1300, 900)
    w.show()
    w.activateWindow()
    w.act_snap.setChecked(False)
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    w.view.set_zoom(1.0)
    w.view.centerOn(QtCore.QPointF(300, 300))
    _pump()
    yield w
    w.view.finish_text_edit(False)
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


def _apply_setting(w, key, value):
    """Change the row of ``key`` in the Settings window as the user does, then Close (the
    change applies as it does live)."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(w)
    dlg.show()
    _pump()
    box = dlg._row_widgets[key]
    if isinstance(box, QtWidgets.QCheckBox):
        box.setChecked(bool(value))
    elif isinstance(box, QtWidgets.QComboBox):
        k = box.findData(value)
        assert k >= 0, (key, value)
        box.setCurrentIndex(k)
    else:
        box.setValue(int(value))
    dlg.close_live()
    _pump()
    from ctgedit.ui.prefs.settings import app_settings

    assert str(app_settings().value(key)) == str(value), key


def _me(view, kind, sp, buttons=QtCore.Qt.NoButton, mods=NOMOD):
    vp = view.mapFromScene(QtCore.QPointF(*sp))
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), LEFT, buttons, mods)


def _click(view, sp):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, sp, LEFT))
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, sp))
    _pump(1)


def _drag(view, a, b):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, a, LEFT))
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), LEFT))
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, b, LEFT))
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, b))
    _pump(1)


def _rect(w, l, t, r, b):
    i = w.doc.create_object("rectangle", (l, t, r, b), None, colour=0x3366CC)
    w._refresh()
    return i


def _box(w, i):
    return w.doc.unit_box(i)


def _shortcut(w, key):
    seq = QtGui.QKeySequence(key)
    for sc in w.view.findChildren(QtGui.QShortcut):
        if sc.key() == seq:
            return sc
    raise AssertionError("no canvas shortcut %s" % key)


def test_every_row_has_its_declared_default_and_a_tip():
    """The rows of this package: declared once, with today's behaviour as the default."""
    from ctgedit.ui.prefs import settings as S
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog, declared_default

    want = {
        "opt/keep_tool": True,
        "opt/big_nudge": 10,
        "opt/arrows_pan_step": 8,
        "canvas/paste_at": "offset",
        "opt/copy_sheets_max": 7,
        "opt/align_to": "selection",
        "opt/place_anchor": "top_left",
        "opt/backup_keep": 10,
        "opt/backup_days": 30,
        "opt/history_every_min": 0,
        "opt/mru_max": 16,
        "opt/print_orientation": "landscape",
    }
    rows = {r.key: r for r in SettingsDialog.ROWS}
    for key, default in want.items():
        assert declared_default(key) == default, key
        assert key in rows and rows[key].tip, key
    from ctgedit.infra import backups
    from ctgedit.ui.window.canvas_edits import CanvasEdits
    from ctgedit.ui.window.shell import WindowShell

    assert (backups.KEEP, backups.MAX_AGE_DAYS, backups.MIN_KEEP) == (10, 30.0, 3)
    assert CanvasEdits.COPY_SHEETS_MAX == 7 and WindowShell.MRU_MAX == 16
    assert S.whole("opt/big_nudge", S.BIG_NUDGE_RANGE) == 10


# ------------------------------------------------------------------- keep the tool
def test_keep_the_tool_after_drawing_and_placing(win):
    w, v = win, win.view
    doc = w.doc
    n0 = len(doc.page.objects)
    # off: back to Select after one rectangle
    _apply_setting(w, "opt/keep_tool", False)
    w.tool_actions["rectangle"].trigger()
    _drag(v, (60, 300), (120, 340))
    assert len(doc.page.objects) == n0 + 1
    assert v.tool() == "select" and w.tool_actions["select"].isChecked()
    # a typed text: back to Select too
    w.tool_actions["text"].trigger()
    _click(v, (60, 400))
    QTest.keyClicks(v, "Hi")
    _click(v, (400, 420))  # a click elsewhere stores it
    assert len(doc.page.objects) == n0 + 2 and v.tool() == "select"
    # kept (the default): two rectangles with one pick of the tool; Esc goes back to Select
    _apply_setting(w, "opt/keep_tool", True)
    assert w._keep_tool and v.KEEP_TOOL
    w.tool_actions["rectangle"].trigger()
    _drag(v, (160, 300), (220, 340))
    _drag(v, (260, 300), (320, 340))
    assert len(doc.page.objects) == n0 + 4
    assert v.tool() == "rectangle" and w.tool_actions["rectangle"].isChecked()
    QTest.keyClick(v, QtCore.Qt.Key_Escape)
    _pump(1)
    assert v.tool() == "select"
    # the Text tool stays for the next text; Esc in an empty one goes back to Select
    w.tool_actions["text"].trigger()
    _click(v, (60, 460))
    QTest.keyClicks(v, "One")
    _click(v, (400, 470))
    assert len(doc.page.objects) == n0 + 5 and v.tool() == "text"
    _click(v, (60, 500))
    assert v.text_editing()
    QTest.keyClick(v, QtCore.Qt.Key_Escape)
    _pump(1)
    assert not v.text_editing() and v.tool() == "select"
    assert len(doc.page.objects) == n0 + 5


def test_keep_the_tool_after_placing_a_symbol(win):
    w, v = win, win.view
    doc = w.doc
    n0 = len(doc.page.roots())
    _apply_setting(w, "opt/keep_tool", False)  # (off: back to Select)
    w._pending_symbol = SYMBOL
    w._set_tool("symbol")
    _click(v, (100, 200))
    assert len(doc.page.roots()) == n0 + 1 and v.tool() == "select"
    assert w._pending_symbol is None
    # (a click places it at its own size, its top left at the click)
    (i,) = w._sel
    w_, h_ = doc.library_element_size(*SYMBOL)
    assert _box(w, i) == (100, 200, 100 + w_, 200 + h_)
    _apply_setting(w, "opt/keep_tool", True)
    w._pending_symbol = SYMBOL
    w._set_tool("symbol")
    _click(v, (100, 200))
    _click(v, (300, 200))
    assert len(doc.page.roots()) == n0 + 3
    assert v.tool() == "symbol" and w._pending_symbol == SYMBOL


# ---------------------------------------------------------------- the arrow keys
def test_large_arrow_key_step(win):
    from ctgedit.ui.prefs.settings import app_settings

    w = win
    i = _rect(w, 300, 300, 340, 330)
    w._set_selection([i])
    w._nudge_step = 1
    app_settings().setValue("opt/ctrl_arrow", "x10")
    x0 = _box(w, i)[0]
    _shortcut(w, "Ctrl+Right").activated.emit()
    w.commit_nudge()
    assert _box(w, i)[0] == x0 + 10  # the default: ten steps
    _apply_setting(w, "opt/big_nudge", 25)
    assert w._big_nudge == 25
    _shortcut(w, "Ctrl+Right").activated.emit()
    w.commit_nudge()
    assert _box(w, i)[0] == x0 + 35
    # Shift + arrow set to the large move takes it too
    app_settings().setValue("opt/shift_arrow", "nudge10")
    _shortcut(w, "Shift+Down").activated.emit()
    w.commit_nudge()
    assert _box(w, i)[1] == 300 + 25
    # the dialog names it a large step
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(w)
    combo = dlg._row_widgets["opt/ctrl_arrow"]
    assert combo.itemText(combo.findData("x10")) == "Move one large step"
    assert (
        dlg._shift_arrow.itemText(dlg._shift_arrow.findData("nudge10")) == "Move the selection one large step"
    )
    dlg.close()


def test_arrow_keys_scroll_by(win):
    w, v = win, win.view
    w._set_selection([])
    v.set_zoom(4.0)
    v.centerOn(QtCore.QPointF(400, 300))
    _pump(1)
    hs = v.horizontalScrollBar()
    h0 = hs.value()
    _shortcut(w, "Right").activated.emit()
    assert hs.value() == h0 + v.viewport().width() // 8  # the default: an eighth
    _apply_setting(w, "opt/arrows_pan_step", 4)
    h1 = hs.value()
    _shortcut(w, "Right").activated.emit()
    assert hs.value() == h1 + v.viewport().width() // 4
    _apply_setting(w, "opt/arrows_pan_step", 16)
    h2 = hs.value()
    _shortcut(w, "Left").activated.emit()
    assert hs.value() == h2 - v.viewport().width() // 16
    # the row needs 'With nothing selected, the arrow keys scroll the page'
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(w)
    pan, step = dlg._row_widgets["opt/arrows_pan"], dlg._row_widgets["opt/arrows_pan_step"]
    assert step.isEnabled()
    pan.setChecked(False)
    assert not step.isEnabled()
    dlg.reject()


# ------------------------------------------------------------------ copy and paste
def _copy(w, indices):
    w._set_selection(indices)
    w.act_copy.trigger()


def _cursor_at(w, sp):
    v = w.view
    QtGui.QCursor.setPos(v.viewport().mapToGlobal(v.mapFromScene(QtCore.QPointF(*sp))))
    _pump(1)


def _menu_paste(w, sp):
    """Right-click at page point ``sp`` and choose Paste in the menu."""
    v = w.view
    gp = v.viewport().mapToGlobal(v.mapFromScene(QtCore.QPointF(*sp)))
    found = []

    def choose():
        menu = QtWidgets.QApplication.activePopupWidget()
        try:
            act = next((a for a in (menu.actions() if menu is not None else ()) if a is w.act_paste), None)
            found.append(act)
        finally:
            if menu is not None:
                menu.hide()
        if found[0] is not None:
            found[0].trigger()

    QtCore.QTimer.singleShot(50, choose)
    v.contextRequested.emit(gp, [])
    _pump()
    assert found and found[0] is not None, "no Paste in the right-click menu"


def test_paste_puts_the_objects_next_to_them_or_at_the_pointer(win):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/picture_options", "import")  # (where it lands, not the options)
    w = win
    i = _rect(w, 100, 100, 140, 130)
    _copy(w, [i])
    _cursor_at(w, (400, 350))
    w.act_paste.trigger()
    (j,) = w._sel
    assert _box(w, j)[:2] == (110, 110)  # the default: next to them
    _apply_setting(w, "canvas/paste_at", "pointer")
    assert w._paste_at == "pointer"
    _copy(w, [i])
    _cursor_at(w, (400, 350))
    w.act_paste.trigger()
    (k,) = w._sel
    assert _box(w, k)[:2] == (400, 350)
    # the right-click menu's Paste: where the menu was opened
    _copy(w, [i])
    _cursor_at(w, (50, 50))
    _menu_paste(w, (250, 420))
    (m,) = w._sel
    assert _box(w, m)[:2] == (250, 420)
    assert w._menu_paste_point is None  # (only while the menu is open)
    # a picture another program copied lands there too
    img = QtGui.QImage(30, 20, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(200, 30, 30))
    QtWidgets.QApplication.clipboard().setImage(img)
    _menu_paste(w, (520, 260))
    (p,) = w._sel
    assert _box(w, p)[:2] == (520, 260)


def test_paste_with_the_default_puts_a_picture_in_the_middle_of_the_view(win):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/picture_options", "import")  # (where it lands, not the options)
    w, v = win, win.view
    img = QtGui.QImage(30, 20, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(30, 30, 200))
    QtWidgets.QApplication.clipboard().setImage(img)
    _menu_paste(w, (60, 60))
    (p,) = w._sel
    c = v.mapToScene(v.viewport().rect().center())
    l, t, r, b = _box(w, p)
    assert abs((l + r) / 2 - c.x()) <= 2 and abs((t + b) / 2 - c.y()) <= 2


def test_open_the_properties_of_at_most(win, monkeypatch):
    from ctgedit.ui.prefs.settings import set_option

    w = win
    set_option("props_on_copy", True)
    opened = []
    monkeypatch.setattr(w, "object_properties", lambda i, *a, **k: opened.append(i))
    rects = [_rect(w, 40 + 50 * k, 100, 80 + 50 * k, 130) for k in range(9)]

    def paste():
        opened.clear()
        _copy(w, rects)
        w.act_paste.trigger()
        _pump()
        return len(opened)

    assert paste() == 7  # the default: the first 7
    assert "first 7 of the 9" in w.statusBar().currentMessage()
    _apply_setting(w, "opt/copy_sheets_max", 0)  # No limit (the Graphics Builder)
    assert paste() == 9
    _apply_setting(w, "opt/copy_sheets_max", 2)
    assert paste() == 2
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(w)
    spin = dlg._row_widgets["opt/copy_sheets_max"]
    assert spin.specialValueText() == "No limit (as the Graphics Builder)"
    assert spin.isEnabled()
    dlg._flags["props_on_copy"].setChecked(False)
    assert not spin.isEnabled()
    dlg.reject()


# ------------------------------------------------------------------------- Align to
def test_align_to_the_selection_the_last_object_or_the_page(win):
    w, v = win, win.view
    doc = w.doc
    a = _rect(w, 100, 100, 140, 130)
    b = _rect(w, 200, 150, 260, 190)
    c = _rect(w, 300, 120, 320, 140)

    def select_last_b():
        v.select_units([[a], [c], [b]], reveal=False)
        _pump(1)
        assert sorted(w._sel) == sorted([a, b, c]) and v.key_root() == b

    align_left, align_right = w.align_actions[0], w.align_actions[2]
    select_last_b()
    align_left.trigger()
    assert [_box(w, i)[0] for i in (a, b, c)] == [100, 100, 100]  # the selection box
    w.act_undo.trigger()
    _apply_setting(w, "opt/align_to", "last")
    select_last_b()
    align_left.trigger()
    assert [_box(w, i)[0] for i in (a, b, c)] == [200, 200, 200]  # B stays
    assert _box(w, b)[:2] == (200, 150)
    # the Align window says so and aligns the same way
    w.act_undo.trigger()
    select_last_b()
    w.act_align_dialog.trigger()
    dlg = w._dialogs["align"]
    assert dlg.reference() == "last"
    assert "the object selected last" in dlg._groups["h"].buttons()[2].toolTip()
    dlg.set_choice(horizontal="right")
    dlg._apply()
    assert [_box(w, i)[2] for i in (a, b, c)] == [260, 260, 260]
    dlg.reject()
    # the page: one object is enough
    _apply_setting(w, "opt/align_to", "page")
    w._set_selection([a])
    assert align_right.isEnabled() and w.act_align_dialog.isEnabled()
    align_right.trigger()
    width = doc.page.appearance.width or doc.parsed.page.page_width
    assert _box(w, a)[2] == width
    # the default keeps one object greyed out
    _apply_setting(w, "opt/align_to", "selection")
    w._set_selection([a])
    assert not align_right.isEnabled() and not w.act_align_dialog.isEnabled()


# --------------------------------------------------------------- placing an element
def test_a_placed_symbol_lands_with_its_top_left_or_its_centre(win):
    w, v = win, win.view
    doc = w.doc
    size = doc.library_element_size(*SYMBOL)
    assert size is not None and size[0] > 4 and size[1] > 4
    v.libraryElementDropped.emit(*SYMBOL, QtCore.QPointF(300, 250))
    _pump()
    (i,) = w._sel
    assert _box(w, i)[:2] == (300, 250)  # the default: its top left
    _apply_setting(w, "opt/place_anchor", "centre")
    v.libraryElementDropped.emit(*SYMBOL, QtCore.QPointF(500, 350))
    _pump()
    (j,) = w._sel
    l, t, r, b = _box(w, j)
    assert abs((l + r) / 2 - 500) <= 1 and abs((t + b) / 2 - 350) <= 1
    # the Symbol tool's click too
    w._pending_symbol = SYMBOL
    w._set_tool("symbol")
    _click(v, (200, 450))
    (k,) = w._sel
    l, t, r, b = _box(w, k)
    assert abs((l + r) / 2 - 200) <= 1 and abs((t + b) / 2 - 450) <= 1


# --------------------------------------------------------------------------- safety
def _old_backups(root, ages_days, tag="a"):
    from ctgedit.infra import backups

    now = time.time()
    for n, age in enumerate(ages_days):
        folder = os.path.join(root, "update-pages-old-%s%02d" % (tag, n))
        os.makedirs(folder)
        with open(os.path.join(folder, backups.MANIFEST), "w", encoding="utf-8") as fh:
            json.dump(
                {"kind": "update-pages", "created": now - age * 86400.0, "entries": [{"page": "P%d" % n}]}, fh
            )


def test_the_backups_kept(win, fx_install, tmp_path):
    """Delete Page's backup prunes the folder: the newest 10, none older than 30 days (the
    newest 3 always) by default; Settings changes both."""
    from ctgedit.infra import backups
    from ctgedit.io import updatepages

    root = backups.backups_root()
    _old_backups(root, [3 * (k + 1) for k in range(12)])  # 3 to 36 days old
    page = tmp_path / "Gone.ctg"
    shutil.copy2(fx_install.page(SMALL_PAGE), page)
    assert updatepages.backup_page_for_delete(str(page))
    assert len(backups.list_backups()) == 10  # the default
    _apply_setting(win, "opt/backup_keep", 20)
    _apply_setting(win, "opt/backup_days", 0)  # Never
    assert (backups.KEEP, backups.MAX_AGE_DAYS) == (20, 0.0)
    _old_backups(root, [40, 50, 60], tag="b")
    assert updatepages.backup_page_for_delete(str(page))
    assert len(backups.list_backups()) == 14  # nothing removed
    _apply_setting(win, "opt/backup_days", 5)
    assert updatepages.backup_page_for_delete(str(page))
    ages = sorted((time.time() - b.created) / 86400.0 for b in backups.list_backups())
    assert len(ages) == 4 and ages[-1] < 5  # 3 new ones and the 3 days old
    _apply_setting(win, "opt/backup_keep", 3)
    assert updatepages.backup_page_for_delete(str(page))
    assert len(backups.list_backups()) == 3


def test_keep_a_new_earlier_version_at_most_every(win):
    from ctgedit.infra import history as H
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.dialog import history_versions as HV

    w = win
    app_settings().setValue("opt/history_versions", 10)
    HV.configure_from_settings()
    path = w.doc.path

    def edit_and_save():
        d = w.doc
        d.move_objects([d.page.roots()[-1]], 1, 0)
        w.save()
        return len(H.list_versions(path))

    assert edit_and_save() == 1 and edit_and_save() == 2  # the default: every save
    _apply_setting(w, "opt/history_every_min", 30)
    assert H._CONFIG["every_min"] == 30
    assert edit_and_save() == 2  # too soon after the last
    # the first save after a Restore keeps what it replaces, the next one does not
    oldest = H.list_versions(path)[-1]
    assert w.restore_version(oldest, ask=False)
    w.save()
    assert len(H.list_versions(path)) == 3
    assert edit_and_save() == 3


# ------------------------------------------------------------------ pages and printing
def test_recent_pages_kept(win, tmp_path):
    from ctgedit.ui.prefs.settings import app_settings

    w = win
    pages = []
    for k in range(20):
        p = tmp_path / ("R%02d.ctg" % k)
        p.write_bytes(b"x")
        pages.append(str(p))
    for p in pages:
        w._push_mru(p)
    assert len(w.recent_pages()) == 16  # the default
    _apply_setting(w, "opt/mru_max", 5)
    assert len(w.recent_pages()) == 5
    w._fill_mru()
    assert len(w._mru_menu.actions()) == 5
    w._push_mru(pages[0])
    assert len(app_settings().value("mru", [], type=list)) == 5
    _apply_setting(w, "opt/mru_max", 40)
    for p in pages:
        w._push_mru(p)
    assert len(w.recent_pages()) == 20


class _NoPrint:
    """The print window: notes the orientation it opens with, and answers as the user."""

    seen: list = []
    choose = None

    def __init__(self, printer, _parent=None):
        self._printer = printer

    def exec(self):
        L = QtGui.QPageLayout
        landscape = self._printer.pageLayout().orientation() == L.Landscape
        _NoPrint.seen.append("landscape" if landscape else "portrait")
        if _NoPrint.choose is None:
            return QtWidgets.QDialog.Rejected
        self._printer.setPageOrientation(L.Landscape if _NoPrint.choose == "landscape" else L.Portrait)
        self._printer.setOutputFileName(_NoPrint.pdf)  # (a PDF, no printer)
        return QtWidgets.QDialog.Accepted


def test_print_pages_in(win, tmp_path, monkeypatch):
    from PySide6 import QtPrintSupport

    w = win
    monkeypatch.setattr(QtPrintSupport, "QPrintDialog", _NoPrint)
    _NoPrint.seen, _NoPrint.choose, _NoPrint.pdf = [], None, str(tmp_path / "p.pdf")
    w.act_print.trigger()
    assert _NoPrint.seen == ["landscape"]  # the default
    _apply_setting(w, "opt/print_orientation", "portrait")
    w.act_print.trigger()
    assert _NoPrint.seen[-1] == "portrait"
    ap = w.doc.page.appearance
    _apply_setting(w, "opt/print_orientation", "page")
    w.act_print.trigger()
    assert _NoPrint.seen[-1] == ("portrait" if ap.height > ap.width else "landscape")
    # the last print's choice: a print in portrait, the next one starts so
    _apply_setting(w, "opt/print_orientation", "last")
    _NoPrint.choose = "portrait"
    w.act_print.trigger()
    _NoPrint.choose = None
    assert os.path.exists(_NoPrint.pdf)
    w.act_print.trigger()
    assert _NoPrint.seen[-1] == "portrait"


def _off_the_canvas(w):
    """The pointer off the canvas (outside the window, as for a command from a menu)."""
    QtGui.QCursor.setPos(w.mapToGlobal(QtCore.QPoint(-40, -40)))
    _pump(1)


def test_paste_special_and_the_picture_options_read_the_pointer_first(win, monkeypatch):
    """At the pointer: where the pointer was when the command ran, not where the window's OK
    was clicked (Paste Special, and the picture options at a plain paste)."""
    from ctgedit.ui.dialog import dialogs as D
    from ctgedit.ui.dialog import picture_import as PI
    from ctgedit.ui.dialog.bitmap_import import build, plain_options
    from ctgedit.ui.prefs.settings import app_settings

    w = win
    _apply_setting(w, "canvas/paste_at", "pointer")
    ok_at = (150, 500)  # the window's OK, over the page

    class PasteSpecial:
        def __init__(self, formats, parent=None):
            self.formats = formats

        def exec(self):
            _cursor_at(w, ok_at)
            return QtWidgets.QDialog.Accepted

        def choice(self):
            return 0  # the objects

    monkeypatch.setattr(D, "PasteSpecialDialog", PasteSpecial)
    i = _rect(w, 100, 100, 140, 130)
    _copy(w, [i])
    _cursor_at(w, (400, 350))
    w.act_paste_special.trigger()
    (j,) = w._sel
    assert _box(w, j)[:2] == (400, 350)
    # from the Edit menu (the pointer off the canvas): next to the copied objects
    k = _rect(w, 300, 60, 340, 90)
    _copy(w, [k])
    _off_the_canvas(w)
    w.act_paste_special.trigger()
    (m,) = w._sel
    assert _box(w, m)[:2] == (310, 70)
    # a picture through the picture options (Picture options open: at every paste)
    app_settings().setValue(PI.WHEN_KEY, "all")

    def choose(_parent, sources, fit=None, action=""):
        _cursor_at(w, ok_at)
        return [build(s, plain_options(s)) for s in sources]

    monkeypatch.setattr(PI, "choose", choose)
    img = QtGui.QImage(30, 20, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(200, 30, 30))
    QtWidgets.QApplication.clipboard().setImage(img)
    _cursor_at(w, (420, 300))
    w.act_paste.trigger()
    (p,) = w._sel
    assert _box(w, p)[:2] == (420, 300)


def test_paste_at_the_pointer_off_the_page_lands_next_to_the_copied_objects(win):
    w, v = win, win.view
    _apply_setting(w, "canvas/paste_at", "pointer")
    page = v.scene().page_rect()
    assert not page.isNull()
    v.set_zoom(0.4)
    v.centerOn(page.center())
    _pump()
    i = _rect(w, 100, 100, 140, 130)
    _copy(w, [i])
    _cursor_at(w, (page.width() + 60, 200))  # on the canvas, right of the page
    assert w.paste_point() is None
    w.act_paste.trigger()
    (j,) = w._sel
    assert _box(w, j)[:2] == (110, 110)
    # the right-click menu's Paste off the page too
    k = _rect(w, 300, 100, 340, 130)
    _copy(w, [k])
    _menu_paste(w, (-60, 200))
    (m,) = w._sel
    assert _box(w, m)[:2] == (310, 110)
    # on the page: the pointer
    _cursor_at(w, (page.width() - 60, 200))
    x, y = w.paste_point()
    assert abs(x - (page.width() - 60)) <= 3 and abs(y - 200) <= 3


def test_paste_from_a_menu_over_the_canvas_lands_next_to_the_copied_objects(win):
    """Edit > Paste picked in a menu that hangs over the canvas: the pointer is on the menu's
    item, not where the user wants the objects. A bar over the canvas is no place either."""
    w, v = win, win.view
    _apply_setting(w, "canvas/paste_at", "pointer")
    i = _rect(w, 100, 100, 140, 130)
    _copy(w, [i])
    menu = QtWidgets.QMenu(w)
    menu.addAction(w.act_copy)
    menu.addAction(w.act_paste)
    menu.popup(v.viewport().mapToGlobal(v.mapFromScene(QtCore.QPointF(300, 250))))
    _pump()
    at = menu.actionGeometry(w.act_paste).center()
    QtGui.QCursor.setPos(menu.mapToGlobal(at))
    _pump(1)
    vp = v.viewport()
    assert vp.rect().contains(vp.mapFromGlobal(QtGui.QCursor.pos()))  # (over the canvas)
    QTest.mouseClick(menu, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at)
    _pump()
    assert not menu.isVisible()
    (j,) = w._sel
    assert _box(w, j)[:2] == (110, 110)  # next to the copied objects
    # Ctrl+V with the pointer there, the menu gone: at the pointer again
    gp = QtGui.QCursor.pos()
    sp = v.mapToScene(vp.mapFromGlobal(gp))
    x, y = w.paste_point()
    assert abs(x - sp.x()) <= 1 and abs(y - sp.y()) <= 1
    # a bar over the canvas (as the selection toolbar): next to the copied objects
    p = v.mapFromScene(QtCore.QPointF(300, 250))
    bar = QtWidgets.QFrame(vp)
    bar.setGeometry(p.x() - 40, p.y() - 15, 120, 30)
    bar.show()
    _pump(1)
    QtGui.QCursor.setPos(vp.mapToGlobal(p))
    _pump(1)
    assert w.paste_point() is None
    bar.hide()
    assert w.paste_point() is not None
    bar.deleteLater()
    menu.deleteLater()


def test_align_greyed_out_says_what_it_needs(win):
    from ctgedit.ui.dialog import palette
    from ctgedit.ui.style import tips

    w = win
    w._set_selection([])
    left, dlg = w.align_actions[0], w.act_align_dialog
    assert not left.isEnabled() and not dlg.isEnabled()
    for a in (left, dlg):
        assert tips.needs(a) == "Select two or more objects."
    assert palette.needs_text(w, left, "align.left") == "Select two or more objects first."
    assert palette.needs_text(w, dlg, "act_align_dialog") == "Select two or more objects first."
    _apply_setting(w, "opt/align_to", "page")
    assert not left.isEnabled() and not dlg.isEnabled()
    for a in (left, dlg):
        assert tips.needs(a) == "Select an object."
    assert palette.needs_text(w, left, "align.left") == "Select an object first."
    assert palette.needs_text(w, dlg, "act_align_dialog") == "Select an object first."
    _apply_setting(w, "opt/align_to", "selection")
    assert tips.needs(left) == "Select two or more objects."
    assert palette.needs_text(w, dlg, "act_align_dialog") == "Select two or more objects first."


def test_the_design_keymap_names_the_large_step():
    from ctgedit.ui.prefs import keymap

    text = keymap.presets()["design"]["description"]
    assert "one large step" in text and "10 steps" not in text


def test_the_safety_rows_in_order_and_on_one_line(win):
    from ctgedit.ui.prefs import settings_pages
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    dlg.show()
    _pump()
    page = next(p for p in dlg._tabs.pages if p.title == "Editing")
    sec = next(s for s in page.sections if s.title == "Safety")
    labels = [r.label_text for r in sec.rows if r.label_text]
    k = labels.index("Earlier versions now use:")
    assert labels[k - 3 : k] == [
        "Keep earlier versions of each page:",
        "Earlier versions use at most:",
        "New earlier version at most every:",
    ]
    assert labels[k + 1 : k + 3] == ["Keep backups of batch changes:", "Remove backups older than:"]
    row = next(r for r in sec.rows if r.key == "opt/history_every_min")
    fm = row.label.fontMetrics()
    assert fm.horizontalAdvance(row.label.text()) <= settings_pages.LABEL_WIDTH - 12
    dlg.reject()


def test_a_garbled_setting_reads_as_its_default(win):
    from ctgedit.infra import backups
    from ctgedit.ui.prefs import settings as S
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    w = win
    st = S.app_settings()
    for key, value in (
        ("opt/keep_tool", "banana"),
        ("opt/copy_sheets_max", -5),
        ("opt/backup_days", -1),
        ("opt/history_every_min", 500),
        ("opt/big_nudge", "x"),
        ("opt/mru_max", 1000),
    ):
        st.setValue(key, value)
    w.apply_editing_options()
    assert w._keep_tool is True and w.view.KEEP_TOOL is True  # (garbled: the default)
    assert w._copy_sheets_max == 7 and w._big_nudge == 10
    assert backups.MAX_AGE_DAYS == 30.0 and w.MRU_MAX == 16
    assert S.whole("opt/history_every_min", S.HISTORY_EVERY_RANGE) == 0
    dlg = SettingsDialog(w)
    rows = dlg._row_widgets
    assert rows["opt/keep_tool"].isChecked()
    assert rows["opt/copy_sheets_max"].value() == 7 and rows["opt/backup_days"].value() == 30
    assert rows["opt/history_every_min"].value() == 0 and rows["opt/mru_max"].value() == 16
    dlg.reject()
    for key in (
        "opt/copy_sheets_max",
        "opt/backup_days",
        "opt/history_every_min",
        "opt/big_nudge",
        "opt/mru_max",
    ):
        st.remove(key)
    # an on / off value: a boolean, or true / false / 1 / 0 in any case; else the default
    for value, want in (
        (True, True),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        (1, True),
        (False, False),
        ("false", False),
        ("0", False),
        (0, False),
        ("banana", True),
        ("", True),
        (7, True),
    ):
        st.setValue("opt/keep_tool", value)
        assert S.option("keep_tool") is want, value
    assert S.OPTION_DEFAULTS["underlay_remember"] is True
    st.setValue("opt/underlay_remember", "banana")
    assert S.option("underlay_remember") is True
    st.setValue("opt/underlay_remember", "False")
    assert S.option("underlay_remember") is False
    st.remove("opt/keep_tool")
    st.remove("opt/underlay_remember")
    # the window's spans are the settings' own
    spans = {r.key: r.span for r in SettingsDialog.ROWS if r.key in S.WHOLE_SPANS}
    assert spans == S.WHOLE_SPANS
