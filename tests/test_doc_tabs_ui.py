"""Document tabs."""

import json
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the small page and the large one (FxMain)
SMALL, LARGE = "!FxSystem", "FxWork"
#: the library and the element the session's element tab opens (FxLib)
LIBRARY, ELEMENT = "fxparts.ctm", "fx_agitator_mixer"


def _pump(n=8):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


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
    w.resize(1280, 720)
    yield w
    for x in list(MainWindow._instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow._instances:
            MainWindow._instances.remove(x)
        x.hide()
        x.deleteLater()
    _delete_now()


def _delete_now():
    """Delete what was given to deleteLater (outside a running event loop processEvents never
    does)."""
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def _copy(fx_install, name, src=SMALL):
    """A copy of FxMain's page ``src`` named ``name`` in the copy's FxMain folder."""
    p = os.path.join(fx_install.project("FxMain"), name + ".ctg")
    shutil.copyfile(fx_install.page(src), p)
    return p


def _centre(win):
    vp = win.view.viewport()
    return win.view.mapToScene(QtCore.QPoint(vp.width() // 2, vp.height() // 2))


# ------------------------------------------------------------------- the canvas menu
def test_the_canvas_menu_is_built_from_sections_others_can_add(win, fx_install):
    from ctgedit.ui.window import context_menu as CM

    assert [s.name for s in CM.sections()] == [
        "guide",
        "locked",
        "stack",
        "quick",
        "object",
        "same",
        "arrange",
        "turn",
        "clipboard",
        "underlay",
        "page",
    ]
    assert win.open_page(_copy(fx_install, "A"))
    seen = []

    def mine(w, menu, ctx):
        seen.append(ctx)
        menu.addAction("My Command")

    CM.register_section("mine", 50, mine)
    CM.register_section("broken", 60, lambda w, m, c: 1 / 0)
    try:
        root = win.doc.page.roots()[-1]
        win._sel = [root]
        menu = CM.build_canvas_menu(win, QtCore.QPoint(10, 10), [root])
        texts = [a.text() for a in menu.actions()]
        assert "My Command" in texts
        assert (
            texts.index("Cut")
            < texts.index("Bring to Front")
            < texts.index("Object Properties…")
            < texts.index("My Command")
            < texts.index("Fit Page")
        )
        assert seen and seen[0]["indices"] == [root] and seen[0]["units"] == {root}
        assert "scene_pos" in seen[0] and isinstance(seen[0]["hits_under"], list)
        # nothing selected: the page's entries only
        menu = CM.build_canvas_menu(win, QtCore.QPoint(10, 10), [])
        texts = [a.text() for a in menu.actions() if a.text()]
        assert "Cut" not in texts and texts[0] == "Paste" and "Page Properties…" in texts
        assert not menu.actions()[-1].isSeparator()
    finally:
        CM.unregister_section("mine")
        CM.unregister_section("broken")


# ------------------------------------------------------------------- drops
def _mime(paths):
    m = QtCore.QMimeData()
    m.setUrls([QtCore.QUrl.fromLocalFile(p) for p in paths])
    return m


def _drag(target, mime, pos=QtCore.QPoint(200, 200)):
    enter = QtGui.QDragEnterEvent(pos, QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    QtCore.QCoreApplication.sendEvent(target, enter)
    drop = QtGui.QDropEvent(
        QtCore.QPointF(pos), QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier
    )
    QtCore.QCoreApplication.sendEvent(target, drop)
    return enter, drop


def test_a_page_dropped_on_the_window_opens_and_a_picture_on_the_canvas_imports(
    win, fx_install, tmp_path, monkeypatch
):
    win.show()
    page = _copy(fx_install, "Large", src=LARGE)
    m0 = _mime([page])
    enter, drop = _drag(win, m0)
    assert enter.isAccepted() and drop.isAccepted()
    _pump()
    assert win.doc is not None and os.path.basename(win.doc.path) == "Large.ctg"
    # on the canvas: a dashed frame says what a drop does, then it opens
    other = _copy(fx_install, "Other")
    vp = win.view.viewport()
    mime = _mime([other])  # (kept: the events hold it)
    enter = QtGui.QDragEnterEvent(
        QtCore.QPoint(100, 100), QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier
    )
    QtCore.QCoreApplication.sendEvent(vp, enter)
    assert enter.isAccepted() and win._drop_frame.isVisible()
    assert win._drop_frame.text == "Drop to open Other"
    drop = QtGui.QDropEvent(
        QtCore.QPointF(100, 100), QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier
    )
    QtCore.QCoreApplication.sendEvent(vp, drop)
    _pump()
    assert not win._drop_frame.isVisible()
    assert os.path.basename(win.doc.path) == "Other.ctg" and len(win._docs) == 2
    # a picture still imports
    png = str(tmp_path / "pic.png")
    img = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(10, 20, 30))
    img.save(png)
    got = []
    monkeypatch.setattr(win, "_import_files", lambda paths, *a, **k: got.append(paths))
    m2 = _mime([png])
    _drag(vp, m2, QtCore.QPoint(120, 120))
    _pump()
    assert got and os.path.normcase(got[0][0]) == os.path.normcase(png)
    assert len(win._docs) == 2


def test_a_dropped_library_opens_the_browser_on_it(win, fx_install, monkeypatch):
    lib = fx_install.file("FxLib", "fxgenie.ctm")
    from ctgedit.ui import browser

    asked = []
    monkeypatch.setattr(
        browser.PageBrowserDialog,
        "get_open_target",
        staticmethod(lambda parent, start, **k: asked.append(start)),
    )
    m = _mime([str(lib)])
    _drag(win, m)
    _pump()
    assert asked and os.path.normcase(asked[0]) == os.path.normcase(str(lib))


# ------------------------------------------------------------------- where a page opens
def test_a_small_page_opens_at_most_at_200_percent(win, fx_install):
    win.resize(1600, 1000)  # (fitted, the page is bigger than 200 %)
    win.show()
    _pump()
    assert win.open_page(_copy(fx_install, "Small"))
    _pump(10)
    assert win._fit_zoom() > 2.0  # (fitted it would be bigger)
    assert abs(win.view.zoom() - 2.0) < 1e-6
    page = win.view.scene().page_rect()
    c = _centre(win)
    assert abs(c.x() - page.center().x()) < 2 and abs(c.y() - page.center().y()) < 2
    # Fit Page still fits it fully
    win.act_zoom_fit.trigger()
    _pump()
    assert abs(win.view.zoom() - win._fit_zoom()) < 0.01


def test_no_limit_fits_and_the_setting_is_read_each_time(win, fx_install):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("view/open_zoom_max", "0")
    win.show()
    _pump()
    assert win.open_page(_copy(fx_install, "Small"))
    _pump(10)
    assert abs(win.view.zoom() - win._fit_zoom()) < 0.01


def test_a_page_opens_where_it_was_left(win, fx_install):
    win.show()
    _pump()
    p = _copy(fx_install, "Large", src=LARGE)
    assert win.open_page(p)
    _pump()
    win.view.set_zoom(3.0)
    win.view.centerOn(QtCore.QPointF(1200, 650))
    _pump()
    before = _centre(win)
    win.close_current_document()
    from ctgedit.ui.prefs.settings import app_settings

    keys = [k for k in app_settings().allKeys() if k.startswith("view/last/")]
    assert len(keys) == 1
    v = json.loads(app_settings().value(keys[0]))
    assert abs(v["z"] - 3.0) < 1e-6 and v["w"] > 0 and v["h"] > 0
    assert win.open_page(p)
    _pump(10)
    assert abs(win.view.zoom() - 3.0) < 1e-6
    c = _centre(win)
    assert abs(c.x() - before.x()) < 2 and abs(c.y() - before.y()) < 2
    # 'Fit the page in the window' fits it instead
    win.close_current_document()
    win._open_zoom = "fit"
    assert win.open_page(p)
    _pump(10)
    assert abs(win.view.zoom() - min(2.0, win._fit_zoom())) < 0.01


def test_a_page_of_another_size_now_is_fitted(win, fx_install):
    from ctgedit.ui.prefs.settings import app_settings

    win.show()
    _pump()
    p = _copy(fx_install, "Large", src=LARGE)
    assert win.open_page(p)
    key = win._view_key(win.doc)
    win.close_current_document()
    app_settings().setValue(key, json.dumps({"z": 3.0, "x": 10, "y": 10, "w": 1, "h": 1, "t": 1}))
    assert win.open_page(p)
    _pump(10)
    assert abs(win.view.zoom() - min(2.0, win._fit_zoom())) < 0.01


def test_at_most_200_views_are_kept_the_oldest_go():
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    for k in range(205):
        st.setValue(
            "view/last/c:/p/page%03d.ctg" % k,
            json.dumps({"z": 1, "x": 0, "y": 0, "w": 1, "h": 1, "t": float(k)}),
        )
    w = MainWindow()
    try:
        w._prune_views(st)
        keys = [k for k in st.allKeys() if k.startswith("view/last/")]
        assert len(keys) == 200
        assert "view/last/c:/p/page000.ctg" not in keys
        assert "view/last/c:/p/page204.ctg" in keys
    finally:
        MainWindow._instances.remove(w)
        w.deleteLater()
        _delete_now()


# ------------------------------------------------------------------- the session
def test_the_session_keeps_the_order_the_pins_an_element_and_the_current_tab(win, fx_install):
    a = _copy(fx_install, "A")
    b = _copy(fx_install, "B")
    lib = fx_install.file("FxLib", LIBRARY)
    assert win.open_page(a) and win.open_page(b)
    win._open_element(str(lib), ELEMENT)
    win._tab_target = win._docs[1]
    win.toggle_pin_tab()  # B pinned: first
    win._tab_target = None
    win.show_tab(1)  # A is current
    win.save_prefs()
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    rows = json.loads(st.value("session/tabs"))
    assert [os.path.basename(r["path"]) for r in rows] == ["B.ctg", "A.ctg", LIBRARY]
    assert [r["pinned"] for r in rows] == [True, False, False]
    assert rows[2]["element"] == ELEMENT
    assert len(rows[0]["transform"]) == 6 and len(rows[0]["scroll"]) == 2
    assert int(st.value("session/current")) == 1
    assert st.value("session/open_files")  # (the crash-safe list of open files)
    from ctgedit.ui.main_window import MainWindow

    MainWindow._session_seen.clear()
    w2 = MainWindow()
    assert w2.restore_session(reopen=True)
    assert [w2._tab_title(e["doc"]) for e in w2._docs] == ["B", "A", ELEMENT]
    assert w2._docs[0].get("pinned")
    assert os.path.basename(w2.doc.path) == "A.ctg"  # the current tab
    w2.show_tab(2)  # the element opens when shown
    from ctgedit.editing import ElementDocument

    assert isinstance(w2.doc, ElementDocument) and w2.doc.element_name == ELEMENT


def test_the_open_files_list_says_which_rows_come_back(win, fx_install):
    """A crash keeps session/open_files current (the autosave tick) while the rows may be
    older: a page closed since does not come back; session/pinned pins too."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    a, b, c = _copy(fx_install, "A"), _copy(fx_install, "B"), _copy(fx_install, "C")
    st = app_settings()
    st.setValue(
        "session/tabs",
        json.dumps([{"path": a, "element": "", "pinned": True}, {"path": b, "element": "", "pinned": False}]),
    )
    st.setValue("session/open_files", [b, c])
    st.setValue("session/pinned", [c])
    MainWindow._session_seen.clear()
    assert win.restore_session(reopen=True)
    assert [win._tab_title(e["doc"]) for e in win._docs] == ["C", "B"]
    assert win._docs[0].get("pinned") and not win._docs[1].get("pinned")
    # the autosave tick keeps the rows current too
    from ctgedit.ui import recovery

    win.show()
    recovery.controller()._write_session()
    rows = json.loads(st.value("session/tabs"))
    assert [os.path.basename(r["path"]) for r in rows] == ["C.ctg", "B.ctg"]
    assert [os.path.basename(p) for p in st.value("session/pinned", [], type=list)] == ["C.ctg"]


def test_on_start_open_nothing_or_the_start_page(fx_install):
    _app()
    from ctgedit import app as A
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    page = _copy(fx_install, "A")
    st.setValue("session/open_files", [page])
    st.setValue("session/last_file", page)
    made = []
    try:
        st.setValue("opt/startup", "none")
        w = A.start_window(["ctgedit"])
        made.append(w)
        assert not w._docs and w.start_page.isHidden()
        st.setValue("opt/startup", "start")
        w = A.start_window(["ctgedit"])
        made.append(w)
        assert not w._docs and not w.start_page.isHidden()
        st.setValue("opt/startup", "session")
        MainWindow._session_seen.clear()
        w = A.start_window(["ctgedit"])
        made.append(w)
        assert [os.path.basename(e["doc"].path) for e in w._docs] == ["A.ctg"]
        # a page named on the command line always opens
        st.setValue("opt/startup", "none")
        other = _copy(fx_install, "B")
        w = A.start_window(["ctgedit", other])
        made.append(w)
        assert [os.path.basename(e["doc"].path) for e in w._docs] == ["B.ctg"]
    finally:
        for w in made:
            w.hide()
            if w in MainWindow._instances:
                MainWindow._instances.remove(w)
            w.deleteLater()
        _delete_now()


# ------------------------------------------------------------------- Settings
def test_the_settings_rows():
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import OPEN_ZOOM, SETTINGS_KEYS, app_settings
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    assert OPEN_ZOOM[0][0] == "last" and SETTINGS_KEYS["opt/open_zoom"] == "last"
    for key, default in (
        ("view/open_zoom_max", "200"),
        ("view/open_zoom_max_element", "400"),
        ("ui/tab_close_button", "hover"),
        ("ui/ctrl_tab_order", "mru"),
        ("opt/startup", "session"),
        ("ui/pinned_tab_compact", True),
        ("session/tabs", ""),
        ("session/current", -1),
    ):
        assert SETTINGS_KEYS[key] == default, key
    w = MainWindow()
    try:
        dlg = SettingsDialog(w)
        rows = dlg._tab_rows
        assert rows.zoom_max.currentData() == "200" and rows.startup.currentData() == "session"
        assert dlg._open_zoom.currentData() == "last"
        rows.close_buttons.setCurrentIndex(rows.close_buttons.findData("never"))
        rows.ctrl_tab.setCurrentIndex(rows.ctrl_tab.findData("position"))
        rows.compact.setChecked(False)
        dlg._apply()
        st = app_settings()
        assert st.value("ui/tab_close_button") == "never"
        assert st.value("ui/ctrl_tab_order") == "position"
        assert st.value("ui/pinned_tab_compact", True, type=bool) is False
        dlg = SettingsDialog(w)
        dlg.restore_defaults()
        assert dlg._tab_rows.close_buttons.currentData() == "hover"
        assert dlg._tab_rows.compact.isChecked() and dlg._open_zoom.currentData() == "last"
        # the Settings search finds them
        labels = []
        for lab in dlg.findChildren(QtWidgets.QLabel):
            labels.append(lab.text())
        for want in ("Largest zoom when a page opens:", "Tab close buttons:", "On start:", "Ctrl+Tab order:"):
            assert want in labels, want
    finally:
        MainWindow._instances.remove(w)
        w.deleteLater()
        _delete_now()


# ------------------------------------------------------------------- windows go along
def test_a_moved_page_takes_its_windows_and_their_callbacks_follow(win, fx_install):
    from ctgedit.ui.modeless import doc_key

    assert win.open_page(_copy(fx_install, "A")) and win.open_page(_copy(fx_install, "B"))
    doc = win.doc
    seen = []

    def opener(self):  # (as a window command opens one: closes over self)
        dlg = QtWidgets.QDialog(self)

        def on_accept(d):
            seen.append(self)

        self._show_modeless(dlg, ("pageprops", doc_key(self.doc)), on_accept=on_accept, scope="page")
        return dlg

    dlg = opener(win)
    win2 = win.move_tab_to_new_window()
    _pump()
    assert dlg in win2._dialogs.values() and dlg not in win._dialogs.values()
    assert dlg.parent() is win2 and win2.doc is doc
    dlg.accept()
    _pump()
    assert seen == [win2]
