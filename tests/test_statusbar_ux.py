"""The status bar."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the project folder the test's pages go in (tmp_path/FxStatus)
PROJECT = "FxStatus"


def _pump(n=8):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def small(fx_page):
    """The small page every test copies: FxThumb of the fx_install copy."""
    return fx_page("FxThumb")


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 800)
    w.show()
    _pump()
    yield w
    for x in list(MainWindow.instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow.instances:
            MainWindow.instances.remove(x)
        x.hide()


def _open(win, tmp_path, small, name="Small"):
    d = tmp_path / PROJECT
    d.mkdir(exist_ok=True)
    p = d / (name + ".ctg")
    shutil.copyfile(small, p)
    win.open_document(str(p))
    _pump()
    return str(p)


def _visible(win, name):
    from ctgedit.ui.panels import status_items

    f = status_items.field(win, name)
    return f is not None and f.isVisible()


def test_no_page_shows_no_page_fields(win):
    """No page: no '0, 0', 'No selection' or '100%'."""
    from ctgedit.ui.panels import status_items

    for name in ("project", "selection", "position", "zoom", "snap", "doc_state", "ans"):
        assert not _visible(win, name), name
    assert _visible(win, "bell")
    texts = [w.text() for w in win.statusBar().findChildren(QtWidgets.QLabel) if w.isVisible()]
    texts += [w.text() for w in win.statusBar().findChildren(QtWidgets.QToolButton) if w.isVisible()]
    assert not any(t in ("0, 0", "100%", "100 %", "No selection") for t in texts), texts
    assert isinstance(win.statusBar(), status_items.StatusBar)


def test_the_fields_show_with_a_page_in_order_with_separators(win, tmp_path, small):
    from ctgedit.ui.panels import status_items

    _open(win, tmp_path, small)
    for name in ("project", "doc_state", "selection", "position", "zoom", "snap"):
        assert _visible(win, name), name
    xs = [
        status_items.field(win, n).x()
        for n in ("bell", "project", "doc_state", "selection", "position", "zoom", "snap")
    ]
    assert xs == sorted(xs)
    f = status_items.field(win, "zoom")
    m = f.layout().contentsMargins()
    from ctgedit.ui.style.theme import sp

    assert m.left() == sp(2) and m.right() == sp(2)  # 8 px padding
    img = f.grab().toImage()
    # the 1 px separator at the field's left edge
    from ctgedit.ui.style import theme

    mid = img.height() // 2
    assert QtGui.QColor(img.pixel(0, mid)).name() == theme.color("border").name()


def test_selection_and_pointer_in_words(win, tmp_path, small):
    _open(win, tmp_path, small)
    assert win.lbl_sel.text() == "No selection"
    objs = win.doc.page.objects
    i = next(
        k
        for k, o in enumerate(objs)
        if o.parent is None and o.geometry is not None and not o.raw.get("_from_template")
    )
    win._set_selection([i])
    _pump()
    g = objs[i].geometry
    t = win.lbl_sel.text()
    assert "\u00b7 %d \u00d7 %d px at %d, %d" % (g.extent_x, g.extent_y, g.x, g.y) in t, t
    win.view.hoverPos.emit(412, 224)
    assert win.lbl_pos.text() == "x 412  y 224 px"
    QtWidgets.QApplication.sendEvent(win.view.viewport(), QtCore.QEvent(QtCore.QEvent.Leave))
    assert win.lbl_pos.text() == ""  # blank off the canvas


def test_selection_click_shows_properties_and_double_click_adjusts(win, tmp_path, small, monkeypatch):
    _open(win, tmp_path, small)
    win.dock_props.hide()
    win.lbl_sel.click()
    assert not win.dock_props.isHidden()
    ran = []
    objs = win.doc.page.objects
    win._set_selection(
        [next(k for k, o in enumerate(objs) if o.parent is None and not o.raw.get("_from_template"))]
    )
    assert win.act_adjust.isEnabled()
    win.act_adjust.triggered.connect(lambda *_: ran.append(1))
    win.lbl_sel.doubleClicked.emit()
    assert ran


def test_the_zoom_menu_fit_and_typed_zoom(win, tmp_path, small):
    _open(win, tmp_path, small)
    z = win.lbl_zoom
    assert z.button.text().endswith("%")
    m = z.button.open_menu()
    _pump()
    assert m.isVisible()
    labels = [a.text() for a in m.actions()]
    assert "100 %" in labels and "200 %" in labels
    assert win.act_zoom_fit in m.actions()
    # a second press on the button while the menu it opened shows: a double-click (Fit Page)
    fits = []
    win.act_zoom_fit.triggered.connect(lambda *_: fits.append(1))
    g = z.button.mapToGlobal(z.button.rect().center())
    assert z.button.menu_press(g) and fits and not m.isVisible()
    # Ctrl+click types a zoom: Enter applies it
    QtTest.QTest.mouseClick(z.button, QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier)
    _pump()
    assert not z.entry.isHidden() and z.button.isHidden()
    z.entry.setText("250 %")
    QtTest.QTest.keyClick(z.entry, QtCore.Qt.Key_Return)
    _pump()
    assert z.entry.isHidden() and not z.button.isHidden()
    win.view.finish_view_animation()
    assert abs(win.view.zoom() - 2.5) < 0.01
    # Esc goes back without a change
    z.edit()
    z.entry.setText("50 %")
    QtTest.QTest.keyClick(z.entry, QtCore.Qt.Key_Escape)
    win.view.finish_view_animation()
    assert abs(win.view.zoom() - 2.5) < 0.01


def test_snap_chips_are_the_menu_commands(win, tmp_path, small):
    from ctgedit.ui.panels import status_items

    _open(win, tmp_path, small)
    chips = status_items.field(win, "snap").inner.chips
    assert [c.text() for c in chips] == ["Snap", "Guidelines", "Smart Guides"]
    assert chips[0].defaultAction() is win.act_snap
    was = win.act_snap.isChecked()
    chips[0].click()
    assert win.act_snap.isChecked() != was and chips[0].isChecked() == win.act_snap.isChecked()
    assert chips[0].text() == "Snap"  # (the short name stays)
    chips[0].click()


def test_the_labels_chip_shows_while_labels_are_not_as_stored(win, tmp_path, small):
    from ctgedit.ui.panels import status_items

    _open(win, tmp_path, small)
    assert not _visible(win, "labels")
    win._label_mode = "native"
    status_items.refresh(win)
    assert _visible(win, "labels")
    assert status_items.field(win, "labels").inner.text() == "Labels: Native Text"
    win._label_mode = "literal"
    status_items.refresh(win)
    assert not _visible(win, "labels")


def test_the_project_menu(win, tmp_path, small, monkeypatch):
    p = _open(win, tmp_path, small)
    shutil.copyfile(small, os.path.dirname(p) + os.sep + "Another.ctg")
    b = win.lbl_project
    assert b.text().endswith(PROJECT)  # 'Install  ·  Project'
    assert PROJECT in b.toolTip()  # (the window's own tip)
    assert b.toolTip().endswith(b.CLICK_TIP)  # (what a click does)
    b.fill()
    texts = [a.text() for a in b.menu_.actions()]
    assert "Pages of " + PROJECT in texts and "Small" in texts and "Another" in texts
    assert "Show in Explorer" in texts and "Copy Path" in texts
    small = next(a for a in b.menu_.actions() if a.text() == "Small")
    assert small.isChecked()
    shown = []
    monkeypatch.setattr(win, "_reveal_in_explorer", lambda path: shown.append(path))
    b.fill()
    next(a for a in b.menu_.actions() if a.text() == "Show in Explorer").trigger()
    assert shown == [os.path.abspath(p)]
    next(a for a in b.menu_.actions() if a.text() == "Copy Path").trigger()
    assert QtWidgets.QApplication.clipboard().text() == os.path.abspath(p)
    opened = []
    monkeypatch.setattr(win, "open_page", lambda path: opened.append(path))
    b.fill()
    next(a for a in b.menu_.actions() if a.text() == "Another").trigger()
    assert opened and opened[0].endswith("Another.ctg")


def test_the_document_state(win, tmp_path, small, monkeypatch):
    """'Saved'; one nudge: '1 unsaved change'; Save: 'Saved'; a read-only page: 'Read-only';
    the tool tip says when the recovery copy was written."""
    from ctgedit.ui.panels import status_items

    _open(win, tmp_path, small)
    chip = win.status_doc_state
    assert chip.text() == "Saved"
    objs = win.doc.page.objects
    i = next(k for k, o in enumerate(objs) if o.parent is None and not o.raw.get("_from_template"))
    win._set_selection([i])
    win._move_key(1, 0, "Right")
    win.commit_nudge()
    status_items.refresh(win)
    assert chip.text() == "1 unsaved change", chip.text()
    import time

    win.doc.ui.recovery_at = time.time()
    chip.refresh(win)
    assert "Recovery copy written at" in chip.toolTip()
    shown = []
    monkeypatch.setattr(win.dock_history, "show", lambda: shown.append(1))
    chip.click()
    assert shown
    assert win.save()
    _pump()
    assert chip.text() == "Saved"
    win.doc.read_only = True
    chip.refresh(win)
    assert chip.text() == "Read-only"


def test_right_click_lists_the_fields_and_the_choice_is_kept(win):
    from ctgedit.ui.panels import status_items
    from ctgedit.ui.main_window import MainWindow

    m = win.statusBar().fields_menu()
    labels = [a.text() for a in m.actions() if a.isCheckable()]
    for want in (
        "Project",
        "Animation points",
        "Selection",
        "Pointer position",
        "Zoom",
        "Snap",
        "Labels",
        "Problems",
        "Document state",
        "Background tasks",
        "Messages",
    ):
        assert want in labels, want
    assert win.act_status_bar in m.actions()
    pos = next(a for a in m.actions() if a.text() == "Pointer position")
    pos.setChecked(False)
    assert not status_items.item_visible("position")
    assert status_items.field(win, "position").user_on is False
    # a new window (the next start) keeps it hidden
    w2 = MainWindow()
    try:
        assert status_items.field(w2, "position").user_on is False
    finally:
        MainWindow.instances.remove(w2)
        w2.hide()
    status_items.set_item_visible("position", True)
    assert status_items.item_visible("position")
    m.deleteLater()


def test_a_field_registered_later_joins_open_windows(win):
    from ctgedit.ui.panels import status_items

    def factory(w):
        lab = QtWidgets.QLabel("Later")
        return lab

    status_items.register("test_later", factory, 65, label="Later field")
    try:
        f = status_items.field(win, "test_later")
        assert f is not None and f.isVisible()
        xs = (status_items.field(win, "bell").x(), f.x())
        assert xs[0] < xs[1]
    finally:
        status_items._ITEMS.pop("test_later", None)
        f = status_items.fields(win).pop("test_later", None)
        if f is not None:
            win.statusBar().removeWidget(f)
            f.deleteLater()


def test_compile_without_a_page_names_the_recent_project(win, tmp_path, small):
    """No page: Compile and Run act on the most recent page's project."""
    from ctgedit.ui.prefs.settings import app_settings

    assert not win.act_compile.isEnabled()  # no recent page
    proj = tmp_path / PROJECT
    proj.mkdir()
    shutil.copyfile(small, proj / "Recent.ctg")
    (proj / "pages.DBF").write_bytes(b"")
    app_settings().setValue("mru", [str(proj / "Recent.ctg")])
    win._compile_target_memo = None
    win._update_actions()
    assert win.act_compile.isEnabled() and win.act_run.isEnabled()
    assert win.act_compile.toolTip() == "Compiles " + PROJECT
    assert win._compile_target() == (str(proj), "")
