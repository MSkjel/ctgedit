"""Timings and counts as settings."""

import gc
import os
import shutil
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from qtkit import app as _app

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LEFT = QtCore.Qt.LeftButton
NOMOD = QtCore.Qt.NoModifier

#: the editor keys of the package: (Settings page, key, the switch row it sits under or '')
KEYS = (
    ("Editing", "opt/expression_check_ms", ""),
    ("Editing", "opt/props_step_pause_ms", "opt/props_arrow_live"),
    ("Editing", "opt/autosave_idle_s", ""),
    ("Canvas", "view/undo_flash_ms", "ui/undo_reveal"),
    ("Canvas", "view/last_views_kept", ""),
    ("Canvas", "opt/underlays_kept", "opt/underlay_remember"),
    ("Canvas", "view/saved_views_max", ""),
    ("Canvas", "ui/navbar_fade_px", ""),
    ("Canvas", "ui/navbar_faded_pct", ""),
    ("Appearance", "objects/rename_click_ms", "objects/rename_click"),
    ("Appearance", "ui/dock_tab_position", ""),
    ("Appearance", "ui/compact_dock_titles", ""),
    ("Ribbon", "ui/hud_delay_ms", ""),
    ("Ribbon", "ui/quick_open_rows", ""),
    ("Ribbon", "ui/palette_rows", ""),
    ("Pages and libraries", "library/recent_kept", ""),
    ("Advanced", "ui/settings_live_delay_ms", "ui/settings_live"),
)
#: the Simulate options of the package and their controls
SIM_KEYS = (
    ("reload_delay_s", "reload_delay"),
    ("cicode_delay_ms", "cicode_delay"),
    ("folder_check_s", "folder_check"),
    ("inspector_refresh_ms", "inspector_refresh"),
    ("watch_refresh_ms", "watch_refresh"),
    ("tag_rows", "tag_rows"),
)


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture(autouse=True, scope="module")
def _collect_at_the_end():
    yield
    for _ in range(2):
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        _pump(3)
        gc.collect()


@pytest.fixture(autouse=True)
def _defaults():
    """The module values back at their defaults around each test."""
    from ctgedit.ui.prefs import timing_options

    timing_options.reset()
    yield
    timing_options.reset()


def _set(key, value):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue(key, value)


@pytest.fixture()
def win():
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
    w.resize(1280, 800)
    yield w
    for d in list(getattr(w, "_dialogs", {}).values()):
        try:
            d.close()
        except RuntimeError:
            pass
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()


def _settings(win, values: dict) -> None:
    """Change rows of the Settings window and apply them, as its Apply does."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    d = SettingsDialog(win)
    try:
        for key, value in values.items():
            assert key in d._row_widgets, key
            d._set_row_value(key, value)
            assert d._row_value(key) == value, key
        d._apply(accept=False)
    finally:
        d.close()
        d.deleteLater()
    _pump(2)


def _page(win, fx_page, name="FxShapes"):
    win.open_document(fx_page(name))
    win.show()
    _pump()
    return win.doc


@pytest.fixture()
def sim():
    """A Simulate window with one tab on a demo simulation: (window, its page)."""
    _app()
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page
    from ctgedit.ui.simulate.sources import FakeSimulation, TagInfo
    from ctgedit.ui.simulate.window import SimulateWindow

    p = Page(name="settings_package")
    p.appearance.width, p.appearance.height = 400, 300
    p.appearance.background_colour = 0xFFFFFF
    o = GraphicObject(
        kind=ObjectKind.RECTANGLE, an=11, geometry=Geometry(x=100, y=100, extent_x=80, extent_y=60)
    )
    o.style.filled, o.style.fill_colour, o.style.line_colour = True, 0x00FF00, 0x00FF00
    o.raw["_type_code"] = 0x0F
    p.objects = [o]
    tags = [
        TagInfo(
            "Tank%03d.Level" % k,
            "REAL",
            0.0,
            eng_zero=0.0,
            eng_full=200.0,
            equipment="Tank%03d" % k,
            item="Level",
            page_reads=True,
        )
        for k in range(600)
    ]
    win = SimulateWindow()
    sp = win.open_page("", page=p, sim=FakeSimulation(tags=tags, extra_tags=0), run=False)
    win.resize(700, 500)
    win.show()
    _pump()
    yield win, sp
    win.close()
    win.deleteLater()
    _pump()


# ------------------------------------------------------------------ the Settings window
def test_each_key_has_one_row_on_its_page_at_the_default_under_its_switch(win):
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    d = SettingsDialog(win)
    try:
        rows = [r.key for r in d._tabs.rows()]
        for page, key, switch in KEYS:
            assert rows.count(key) == 1, key
            on_page = [r.key for r in d._tabs.widget(d._tabs.indexOf(page)).rows()]
            assert key in on_page, (page, key)
            assert d._row_value(key) == SETTINGS_KEYS[key], key
            if switch:
                # (a sub row right under the switch it needs, greyed while it is off)
                assert on_page[on_page.index(key) - 1] == switch, key
                w = d._row_widgets[key]
                box = d._row_widgets.get(switch) or d._flags[switch[4:]]
                box.setChecked(False)
                assert not w.isEnabled(), key
                box.setChecked(True)
                assert w.isEnabled(), key
        # the Simulate page: each option's control shows its default, under its switch
        from ctgedit.ui.simulate import options

        sim_rows = d._tabs.widget(d._tabs.indexOf("Simulate")).rows()
        fields = [r.field for r in sim_rows]
        for name, attr in SIM_KEYS:
            box = getattr(d._sim, attr)
            assert box in fields, name
            assert float(box.value()) == float(options.OPTIONS[name][1]), name
        assert fields.index(d._sim.cicode_delay) == fields.index(d._sim.reload_cicode) + 1
        assert fields.index(d._sim.reload_delay) == fields.index(d._sim.reload_auto) + 1
        d._sim.reload_page.setChecked(False)
        assert not d._sim.reload_delay.isEnabled()
        d._sim.reload_page.setChecked(True)
        assert d._sim.reload_delay.isEnabled()
        # the autosave minutes box is not stopped at 60
        assert d._autosave.maximum() >= 600
    finally:
        d.close()
        d.deleteLater()


def test_the_helper_tips_of_the_settings_window_name_helper_mb(win):
    from ctgedit.ui.prefs.settings import HELPER_MB
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    d = SettingsDialog(win)
    try:
        for box in (d._find_procs, d._update_procs):
            assert ("%d MB" % HELPER_MB) in box.toolTip() and "300 MB" not in box.toolTip()
    finally:
        d.close()
        d.deleteLater()


def test_the_settings_windows_own_pause_is_a_setting(win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    d = SettingsDialog(win)
    try:
        assert d._live_timer.interval() == 150
        d._set_row_value("ui/settings_live_delay_ms", 400)
        d._apply(accept=False)
        assert d._live_timer.interval() == 400  # (this window, at once)
    finally:
        d.close()
        d.deleteLater()
    d = SettingsDialog(win)
    try:
        assert d._live_timer.interval() == 400  # (the next window opens with it)
    finally:
        d.close()
        d.deleteLater()


# ------------------------------------------------------------------ the editor's pauses
def test_the_expression_check_pause_follows_its_setting(win):
    from ctgedit.ui.objprops.expression import ExpressionField

    class Checker:
        def check(self, text, kind, callback):
            callback({"ok": True, "errors": [], "warnings": []})

    f = ExpressionField(None)
    f.set_checker(Checker())
    try:
        QtWidgets.QLineEdit.setText(f, "Tag1 + 2")
        assert f._check_timer.isActive() and f._check_timer.interval() == 700
        _settings(win, {"opt/expression_check_ms": 1200})
        QtWidgets.QLineEdit.setText(f, "Tag1 + 3")
        assert f._check_timer.interval() == 1200
    finally:
        f.deleteLater()


def test_the_number_fields_step_pause_follows_its_setting(win):
    from ctgedit.ui.number_field import NumberField

    f = NumberField(lo=0, hi=1000, value=100, live_steps=True)
    f.show()
    f.setFocus()
    try:
        QTest.keyClick(f, QtCore.Qt.Key_Up)
        assert f._step_timer.isActive() and f._step_timer.interval() == 350
        _settings(win, {"opt/props_step_pause_ms": 900})
        QTest.keyClick(f, QtCore.Qt.Key_Up)
        assert f._step_timer.interval() == 900
    finally:
        f.hide()
        f.deleteLater()


def test_the_objects_panels_rename_pause_follows_its_setting(win, fx_page):
    from ctgedit.ui.panels import objects_dock as OD

    doc = _page(win, fx_page, "FxMany")
    dock, tree = win.objects_dock, win.objects_dock._tree
    assert dock.rename_click_ms == 600
    _settings(win, {"objects/rename_click_ms": 1500})
    assert dock.rename_click_ms == 1500  # (the window's own apply)
    (r,) = doc.find_objects_by_name("FxPlainRect")
    win._on_canvas_selection([r])
    win.view.set_selection([r])
    _pump(3)
    # the slow click starts its timer with the pause chosen (the row's name zone)
    item = dock._items[r]
    tree.scrollToItem(item)
    _pump(2)
    rect = tree.visualRect(tree.indexFromItem(item, 0))
    pos = dock._delegate.zones(rect, r, dock._looks[r].flags)["text"].center()
    QTest.mouseClick(tree.viewport(), LEFT, NOMOD, pos)
    _pump(2)
    assert tree._rename_timer.isActive() and tree._rename_timer.interval() == 1500
    tree._rename_timer.stop()
    assert OD.SLOW_CLICK_MS == 600  # (the default kept)


def test_the_autosave_idle_pause_is_a_setting_and_never_turns_it_off(win, fx_page, monkeypatch):
    from ctgedit.infra import autosave
    from ctgedit.ui import recovery
    from ctgedit.ui.prefs import timing_options

    page = fx_page("FxShapes")
    win.open_document(page)
    win.doc.move_objects([win.doc.page.roots()[-1]], 2, 2)
    assert win.doc.dirty
    clock = {"t": 1000.0}
    monkeypatch.setattr(recovery, "_now", lambda: clock["t"])
    ctl = recovery.controller()
    assert ctl._timer.interval() == recovery.TICK_MS

    def mine():
        return [s for s in autosave.snapshots() if os.path.normcase(s.path) == os.path.normcase(page)]

    try:
        _settings(win, {"opt/autosave_idle_s": 0})  # Never: only by the minutes
        assert timing_options.AUTOSAVE_IDLE_S == 0
        ctl.tick()
        clock["t"] += recovery.IDLE_SECONDS + 5
        ctl.tick()
        assert not mine()
        _settings(win, {"opt/autosave_idle_s": 3})
        assert ctl._timer.interval() == 1500  # (the hook: half the pause)
        win.doc.move_objects([win.doc.page.roots()[-1]], 1, 1)
        ctl.tick()
        clock["t"] += 2
        ctl.tick()
        assert not mine()  # (2 s: not yet)
        clock["t"] += 2
        ctl.tick()
        assert len(mine()) == 1  # (4 s: written)
    finally:
        for s in mine():
            autosave.remove(s.key)
        win.doc.dirty = False


def test_the_undo_flash_time_follows_its_setting(win, fx_page):
    from ctgedit.ui.panels import history_dock as HD

    doc = _page(win, fx_page)
    r = doc.page.roots()[0]
    doc.move_objects([r], 3, 3)
    win._refresh()
    _pump()
    step = doc._undo[-1]
    _settings(win, {"view/undo_flash_ms": 1500})
    t0 = time.monotonic()
    assert win._show_records(HD._step_records(step))
    _boxes, until = HD._FLASH[win.view]
    assert 1.3 <= until - t0 <= 1.7


def test_the_selection_toolbar_settle_pause_follows_its_setting(win, fx_page):
    doc = _page(win, fx_page)
    r = doc.page.roots()[0]
    win.act_selection_hud.setChecked(True)
    win._on_canvas_selection([r])
    win.view.set_selection([r])
    _pump(2)
    assert win._hud_timer.interval() == 250
    _settings(win, {"ui/hud_delay_ms": 800})
    win._schedule_hud()
    assert win._hud_timer.interval() == 800


def test_the_navigation_bar_fades_within_the_distance_chosen(win, fx_page):
    _page(win, fx_page)
    bar = win.navbar
    vp = win.view.viewport()
    _pump(2)
    g = bar.geometry()
    if g.isEmpty() or not bar.isVisible():
        pytest.skip("no navigation bar shown offscreen")

    def move_to(pos):
        ev = QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove,
            QtCore.QPointF(pos),
            QtCore.QPointF(vp.mapToGlobal(pos)),
            QtCore.Qt.NoButton,
            QtCore.Qt.NoButton,
            NOMOD,
        )
        QtWidgets.QApplication.sendEvent(vp, ev)

    above = QtCore.QPoint(g.center().x(), g.top() - 30)  # 30 px from the bar
    move_to(above)
    assert bar._faded  # (within 40 px: faded)
    move_to(QtCore.QPoint(g.center().x(), max(0, g.top() - 300)))
    assert not bar._faded
    _settings(win, {"ui/navbar_fade_px": 10})
    move_to(above)
    assert not bar._faded  # (30 px is outside 10 px)
    _settings(win, {"ui/navbar_fade_px": 100})
    move_to(above)
    assert bar._faded
    # how far it fades is the row under it, and a faded bar takes it at once
    assert abs(bar.graphicsEffect().opacity() - 0.35) < 1e-6
    _settings(win, {"ui/navbar_faded_pct": 60})
    assert bar._faded and abs(bar.graphicsEffect().opacity() - 0.60) < 1e-6
    move_to(QtCore.QPoint(g.center().x(), max(0, g.top() - 300)))
    move_to(above)
    assert abs(bar.graphicsEffect().opacity() - 0.60) < 1e-6


# ------------------------------------------------------------------ the counts
def test_quick_open_and_the_palette_list_as_many_rows_as_chosen(win, fx_project):
    from ctgedit.ui.prefs import timing_options
    from ctgedit.ui.dialog.quick_open import KIND_ORDER, QuickOpen

    q = QuickOpen(win)
    q.set_project_dir(fx_project("FxMain"))
    everything = q._sources.hits("", tuple(KIND_ORDER), limit=100000)
    assert len(everything) > 12
    assert len(q._sources.hits("", tuple(KIND_ORDER))) == min(40, len(everything))
    _settings(win, {"ui/quick_open_rows": 12, "ui/palette_rows": 10})
    assert timing_options.quick_open_limits() == (12, 60, 18)
    assert len(q._sources.hits("", tuple(KIND_ORDER))) == 12
    p = win.palette_popup
    p.open_at()  # (harvests the commands)
    try:
        rows = p._command_rows("a")
        assert 0 < len([r for r in rows if r.kind == "command"]) <= 10
        _settings(win, {"ui/palette_rows": 25})
        assert len([r for r in p._command_rows("a") if r.kind == "command"]) > 10
    finally:
        p.hide()


def test_the_start_page_shows_as_many_cards_as_recent_pages_kept(win, fx_page, tmp_path):
    from ctgedit.ui.panels import start_page as SP

    src = fx_page("FxShapes")
    paths = []
    for k in range(12):
        dst = tmp_path / ("Recent%02d.ctg" % k)
        shutil.copyfile(src, dst)
        paths.append(str(dst))
    _set("mru", paths)
    _set("opt/mru_max", 16)
    assert len(SP.recent_pages()) == 12  # (8 before)
    _settings(win, {"opt/mru_max": 10})
    assert len(SP.recent_pages()) == 10
    win.show()
    _pump(2)
    win.start_page.refresh()
    assert len(win.start_page._cards) == 10


def test_the_remembered_views_underlays_saved_views_and_recent_elements_follow_their_counts(win, fx_page):
    """The four counts are settings, and lowering one takes effect at once (the hook prunes
    the views and underlays, trims the Library panel's Recent, and a stored view list is cut
    when it is read), not at the next store or placement."""
    import json
    from ctgedit.ui.editor import saved_views, underlay
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()

    def kept(group):
        st.beginGroup(group)
        try:
            return sorted(st.allKeys())
        finally:
            st.endGroup()

    doc = _page(win, fx_page)
    for k in range(70):
        st.setValue("view/last/p%03d" % k, json.dumps({"t": float(k), "z": 1.0}))
        st.setValue("underlay/p%03d" % k, json.dumps({"t": float(k)}))
    views = [{"name": "v%d" % k, "zoom": 1.0, "cx": 10, "cy": 10} for k in range(15)]
    saved_views.store_views(doc, views)
    panel = win.library_dock
    for k in range(15):
        panel.note_placed("fxsym", "fx_elem_%d" % k, "ctl")
    # the defaults keep them all (200 / 200 / 40 / 40)
    win._prune_views(st)
    underlay.prune_remembered(st)
    assert len(kept("view/last")) == 70 and len(kept("underlay")) == 70
    assert len(saved_views.load_views(doc)) == 15 and len(panel._recent) == 15
    # lowered: at once, without a store or a placement
    _settings(
        win,
        {
            "view/last_views_kept": 50,
            "opt/underlays_kept": 60,
            "view/saved_views_max": 10,
            "library/recent_kept": 10,
        },
    )
    views_kept = kept("view/last")
    assert len(views_kept) == 50 and views_kept[0] == "p020"  # (the oldest went)
    assert len(kept("underlay")) == 60
    assert len(saved_views.load_views(doc)) == 10
    assert len(panel._recent) == 10
    # and a later store / placement keeps to the new counts
    saved_views.store_views(doc, views)
    assert len(saved_views.load_views(doc)) == 10
    panel.note_placed("fxsym", "fx_elem_new", "ctl")
    assert len(panel._recent) == 10 and panel._recent[0][2] == "fx_elem_new"


def test_the_module_values_default_to_the_declared_keys():
    """ui/prefs/timing_options' defaults are the declared ones (one place for each number)."""
    from ctgedit.ui.prefs import timing_options as TO
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS, WHOLE_SPANS

    TO.reset()
    for key in TO.KEYS:
        lo, hi = WHOLE_SPANS[key]
        assert lo <= SETTINGS_KEYS[key] <= hi, key
    assert TO.QUICK_OPEN_ROWS == SETTINGS_KEYS["ui/quick_open_rows"]
    assert TO.LIBRARY_RECENT_KEPT == SETTINGS_KEYS["library/recent_kept"]
    assert TO.AUTOSAVE_IDLE_S == SETTINGS_KEYS["opt/autosave_idle_s"]


# ------------------------------------------------------------------ the panels' look
def test_the_panel_tab_position_and_compact_titles_are_read(win, sim):
    from ctgedit.ui.panels import docking

    sw, _sp = sim
    North, South = QtWidgets.QTabWidget.North, QtWidgets.QTabWidget.South
    assert win.tabPosition(QtCore.Qt.LeftDockWidgetArea) == North
    bar = win.dock_objects.titleBarWidget()
    assert isinstance(bar, docking.DockTitleBar) and bar.compact
    assert bar.layout().contentsMargins().top() == docking.TITLE_COMPACT[0][1]
    _settings(win, {"ui/dock_tab_position": "bottom", "ui/compact_dock_titles": False})
    assert win.tabPosition(QtCore.Qt.LeftDockWidgetArea) == South
    assert win.tabPosition(QtCore.Qt.BottomDockWidgetArea) == South
    assert not bar.compact
    assert bar.layout().contentsMargins().top() == docking.TITLE_ROOMY[0][1]
    assert bar.close_btn.iconSize().width() == docking.TITLE_ROOMY[1]
    # the Simulate window's panels take them too
    sw.apply_settings()
    assert sw.tabPosition(QtCore.Qt.LeftDockWidgetArea) == South
    sbar = sw.dock_outputs.titleBarWidget()
    assert isinstance(sbar, docking.DockTitleBar) and not sbar.compact
    _settings(win, {"ui/dock_tab_position": "top", "ui/compact_dock_titles": True})
    assert win.tabPosition(QtCore.Qt.LeftDockWidgetArea) == North and bar.compact
    assert bar.close_btn.iconSize().width() == docking.TITLE_COMPACT[1]
    # a new window reads them at its start
    _set("ui/dock_tab_position", "bottom")
    from ctgedit.ui.main_window import MainWindow

    w2 = MainWindow()
    try:
        assert w2.tabPosition(QtCore.Qt.RightDockWidgetArea) == South
    finally:
        if w2 in MainWindow.instances:
            MainWindow.instances.remove(w2)
        w2.hide()
        w2.deleteLater()


# ------------------------------------------------------------------ Update Pages
def test_the_update_pages_box_writes_the_setting_on_ok_only(tmp_path, monkeypatch):
    _app()
    from test_update_pages_parts import tmp_install
    from ctgedit.io import catalog
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog
    from ctgedit.ui.prefs.settings import HELPER_MB, app_settings

    user = tmp_install(tmp_path)
    cat = catalog.ProjectCatalog(user, other_installs=False)
    monkeypatch.setattr(catalog, "catalog_for", lambda *a, **k: cat)
    st = app_settings()
    st.remove("opt/update_processes")
    dlg = UpdatePagesDialog(os.path.join(user, "A"), None)
    try:
        assert ("%d MB" % HELPER_MB) in dlg._procs.toolTip()
        assert "300 MB" not in dlg._procs.toolTip()
        dlg._procs.setValue(2)
        dlg.reject()
        assert not st.contains("opt/update_processes")  # (Cancel: untouched)
        dlg._procs.setValue(3)
        dlg.accept()
        assert int(st.value("opt/update_processes")) == 3  # (OK: kept for next time)
    finally:
        dlg.deleteLater()


# ------------------------------------------------------------------ the Simulate window
def test_log_lines_kept_takes_any_value_and_reaches_the_open_window(sim):
    import collections
    from ctgedit.ui.simulate import ctsim_source, options
    from ctgedit.ui.simulate.panels import OutputsPanel
    from ctgedit.ui.simulate.sources import LogEntry

    sw, sp = sim
    options.put("log_lines", 500)
    assert options.log_lines() == 500
    panel = OutputsPanel()
    try:
        assert panel._all.maxlen == 500  # (5000 before)
    finally:
        panel.deleteLater()
    assert sw.outputs._all.maxlen == 5000  # (made with the default)
    sw.apply_settings()
    assert sw.outputs._all.maxlen == 500
    sw.outputs.add_entries([LogEntry(float(k), "log", "line %d" % k) for k in range(600)])
    assert len(sw.outputs._all) == 500
    options.put("log_lines", 2000)
    sw.apply_settings()
    assert sw.outputs._all.maxlen == 2000 and len(sw.outputs._all) == 500
    # each simulation's own log and the engine source's keep as many
    sp.log = [LogEntry(float(k), "log", "old %d" % k) for k in range(600)]
    options.put("log_lines", 500)
    ctsim_source._LOG_LINES[1] = -1.0
    assert ctsim_source._log_lines() == 500
    assert isinstance(sw.outputs._all, collections.deque)


def test_the_reload_pauses_follow_their_settings(sim):
    from ctgedit.ui.simulate import live_reload, options

    sw, sp = sim
    assert live_reload.page_delay_s() == 1.0
    assert sw.live._sweep.interval() == 2000
    options.put("reload_delay_s", 2.5)
    options.put("cicode_delay_ms", 1500)
    options.put("folder_check_s", 5)
    assert live_reload.page_delay_s() == 2.5
    t0 = time.monotonic()
    sw._schedule_reload(sp, "a test")
    assert 2.3 <= sp.reload_due - t0 <= 2.7
    sp.reload_due = None

    class Session:
        alive = True
        attached = False
        unit = ()
        project = ""

    s = Session()
    sw.live.sync([s])
    watch = sw.live.watch_of(s)
    assert watch is not None and watch.timer.interval() == 1500
    sw.apply_settings()
    assert sw.live._sweep.interval() == 5000
    options.put("cicode_delay_ms", 300)
    sw.apply_settings()
    assert watch.timer.interval() == 300
    sw.live.sync([])


def test_the_inspector_and_watch_refresh_follow_their_settings(sim):
    from ctgedit.ui.simulate import options

    sw, _sp = sim
    insp = sw.open_inspector()
    assert insp._timer.interval() == 500 and sw.watch._timer.interval() == 1000
    options.put("inspector_refresh_ms", 250)
    options.put("watch_refresh_ms", 2000)
    sw.apply_settings()
    assert insp._timer.interval() == 250 and sw.watch._timer.interval() == 2000
    insp.close()


def test_the_inputs_list_is_cut_at_the_rows_chosen(sim):
    from ctgedit.ui.simulate import ctsim_source, options

    sw, _sp = sim
    sw.inputs.page_only.setChecked(False)
    sw.inputs.rebuild()
    assert "600 tags" in sw.inputs.count.text() and "Showing" not in sw.inputs.count.text()
    options.put("tag_rows", 500)
    assert options.tag_rows() == 500 and ctsim_source._tag_rows() == 500
    sw.inputs.rebuild()
    assert sw.inputs.count.text().startswith("Showing 500 of 600 tags")
    options.put("tag_rows", 7)  # (below the span: its floor)
    assert options.tag_rows() == options.TAG_ROWS_RANGE[0]
