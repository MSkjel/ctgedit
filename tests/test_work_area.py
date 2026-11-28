"""The work surface around the page."""

import os
import shutil
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.model.document import (  # noqa: E402
    Geometry,
    GraphicObject,
    ObjectKind as K,
    Page,
    PageAppearance,
    Style,
)
from qtkit import app as _app, canvas_picture

NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton


def _pump(n=3):
    app = _app()
    for _ in range(n):
        app.processEvents()


def _rect(x, y, w, h, colour=0xFF0000):
    return GraphicObject(
        kind=K.RECTANGLE,
        geometry=Geometry(x=x, y=y, extent_x=w, extent_y=h),
        style=Style(filled=True, fill_colour=colour, line_colour=colour),
        raw={"_type_code": 0x0F, "_dwflags": 0x02},
    )


def _page(objs=(), w=400, h=300, bg=0xFFFFFF):
    return Page(
        name="synthetic",
        appearance=PageAppearance(width=w, height=h, background_colour=bg),
        objects=list(objs),
    )


def _scene(page, **kw):
    from ctgedit.render.scene import PageScene

    _app()
    sc = PageScene()
    sc.set_page(page, **kw)
    return sc


_VIEWS = []


def _view(scene, size=(1600, 900), cache=True):
    from ctgedit.ui.editor.canvas import CanvasView

    _app()
    v = CanvasView()
    if not cache:
        v.setCacheMode(QtWidgets.QGraphicsView.CacheNone)
    v.setScene(scene)
    v.resize(*size)
    v.show()
    _pump()
    _VIEWS.append(v)
    return v


@pytest.fixture(autouse=True)
def _close_views():
    yield
    while _VIEWS:
        v = _VIEWS.pop()
        try:
            v.hide()
            v.setScene(None)
            v.deleteLater()
        except RuntimeError:
            pass
    _pump(1)


def _mouse(view, kind, vp, buttons=QtCore.Qt.NoButton, button=QtCore.Qt.NoButton, mods=NOMOD):
    p = QtCore.QPointF(vp)
    g = QtCore.QPointF(view.viewport().mapToGlobal(vp))
    _app().sendEvent(view.viewport(), QtGui.QMouseEvent(kind, p, p, g, button, buttons, mods))


def _visible(view) -> QtCore.QRectF:
    return view.mapToScene(view.viewport().rect()).boundingRect()


def _centre(view) -> QtCore.QPointF:
    vp = view.viewport()
    return view.mapToScene(QtCore.QPoint(vp.width() // 2, vp.height() // 2))


@pytest.fixture()
def pages(fx_page):
    """The fixture pages: .work (FxWork), .wide (FxWide), .small (FxThumb)."""
    return SimpleNamespace(work=fx_page("FxWork"), wide=fx_page("FxWide"), small=fx_page("FxThumb"))


@pytest.fixture()
def work_page(pages):
    from ctgedit.io.reader import read_page

    return read_page(pages.work)


@pytest.fixture()
def wide_scene(pages):
    from ctgedit.io.reader import read_page

    page = read_page(pages.wide)
    sc = _scene(page)
    yield page, sc
    sc.clear()


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1600, 1000)
    w.show()
    _pump()
    yield w
    for e in list(getattr(w, "_docs", [])):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump(1)


# ---------------------------------------------------------------- 1. the work area
def test_work_area_covers_ctdraw_surface_and_drawn_content(work_page, wide_scene):
    sc = _scene(work_page)
    w, h = work_page.appearance.width, work_page.appearance.height
    assert (w, h) == (1366, 746)
    work = sc.work_rect()
    assert work.contains(QtCore.QRectF(0, 0, w * 11 // 10, h * 11 // 10))  # 1502 x 820
    drawn = sc.itemsBoundingRect()
    assert drawn.bottom() > h  # the cells below the page
    assert work.contains(drawn)
    assert sc.sceneRect() == work
    margin = max(40, round(0.05 * w))
    assert work.left() == -margin and work.bottom() == pytest.approx(
        max(drawn.bottom(), h * 11 // 10) + margin
    )

    page, big = wide_scene
    W, H = page.appearance.width, page.appearance.height
    assert (W, H) == (1920, 1080)
    work = big.work_rect()
    assert work.contains(QtCore.QRectF(0, 0, 2112, 1188))
    assert work.contains(big.itemsBoundingRect())
    # the undrawn DummyMarkers at x -847 / 3042 add no empty surround
    assert work.left() >= -96 - 1e-6 and work.right() < 3042

    # a library element window: its canvas + a member at negative coordinates
    el = _scene(_page([_rect(-150, -80, 50, 40)], w=200, h=100))
    el.set_element_mode(True, (0, 0))
    ew = el.work_rect()
    assert ew.contains(QtCore.QRectF(-150, -80, 50, 40))
    assert ew.right() == pytest.approx(240)  # no 1.1 surface: canvas + 40


def test_work_area_grows_on_update_units_and_is_clamped():
    far = _rect(30500, 100, 3000, 50)
    page = _page([_rect(10, 10, 20, 20)])
    sc = _scene(page)
    before = sc.work_rect()
    seen = []
    sc.workAreaChanged.connect(seen.append)
    page.objects.append(far)
    assert sc.update_units(page, [1])
    work = sc.work_rect()
    assert work.contains(before) and work.right() == 32000 and seen  # clamped
    # nothing new beyond it: no change, no re-index
    seen.clear()
    assert sc.update_units(page, [0])
    assert sc.work_rect() == work and not seen


# ------------------------------------------------------------- 2. pan at every zoom
def test_pan_at_every_zoom(work_page):
    sc = _scene(work_page)
    v = _view(sc)
    w, h = work_page.appearance.width, work_page.appearance.height
    for z in (0.05, 0.3, "fit", 1.0, 4.0):
        if z == "fit":
            v.fit()
        else:
            v.set_zoom(z)
        _pump()
        hs, vs = v.horizontalScrollBar(), v.verticalScrollBar()
        assert hs.maximum() > hs.minimum() and vs.maximum() > vs.minimum(), z
        for pt in (QtCore.QPointF(0, 0), QtCore.QPointF(w, h)):
            v.centerOn(pt)
            c = _centre(v)
            tol = 2.0 / v.zoom()
            assert abs(c.x() - pt.x()) <= tol and abs(c.y() - pt.y()) <= tol, (z, pt, c)


# --------------------------------------------------------- 3. zoom under the mouse
def test_zoom_under_mouse_keeps_point(work_page):
    sc = _scene(work_page)
    v = _view(sc)
    v.set_zoom(1.0)
    w, h = work_page.appearance.width, work_page.appearance.height
    for target in (QtCore.QPointF(w + 60, h + 50), QtCore.QPointF(w / 2, h / 3)):
        v.centerOn(target)
        at = v.mapFromScene(target)
        start = v.mapToScene(at)
        for f in (1.15,) * 10 + (1 / 1.15,) * 10 + (0.5, 2.0, 3.0):
            v.zoom_by(f, at=at)
            p = v.mapToScene(at)
            z = v.zoom()
            # never more than the half-pixel a whole-pixel scroll position rounds
            assert abs(p.x() - start.x()) * z <= 0.5 + 1e-6, (target, f)
            assert abs(p.y() - start.y()) * z <= 0.5 + 1e-6, (target, f)


# ------------------------------------------------------------------ 4. Fit Page
def test_fit_page_zoom_unchanged(work_page):
    sc = _scene(work_page)
    v = _view(sc)
    w, h = work_page.appearance.width, work_page.appearance.height

    def expected():
        # fitInView's rule (a 2 px margin) on the view as it is without scroll bars
        vp = v.maximumViewportSize()
        return min((vp.width() - 4) / (w + 80), (vp.height() - 4) / (h + 80))

    v.fit()
    _pump()
    assert v.zoom() == pytest.approx(expected(), rel=1e-9)
    c = _centre(v)
    assert abs(c.x() - w / 2) <= 2 / v.zoom() and abs(c.y() - h / 2) <= 2 / v.zoom()
    # auto-fit tracks the window until a manual zoom
    v.resize(1100, 700)
    _pump()
    assert v.zoom() == pytest.approx(expected(), rel=1e-9)
    v.zoom_by(1.25)
    z = v.zoom()
    v.resize(1400, 800)
    _pump()
    assert v.zoom() == pytest.approx(z)


# ---------------------------------------------- 5. Fit All / Zoom to Selection off page
def test_zoom_to_selection_centres_an_off_page_object():
    sc = _scene(_page([_rect(100, 100, 50, 50), _rect(-300, -200, 100, 100)]))
    v = _view(sc)
    v.fit()
    r = QtCore.QRectF(-300, -200, 100, 100)
    v.frame_rect(r, max_zoom=4.0)  # Zoom to Selection
    assert v.zoom() == pytest.approx(4.0)
    c = _centre(v)
    assert abs(c.x() - r.center().x()) * 4 <= 1.5 and abs(c.y() - r.center().y()) * 4 <= 1.5
    # Fit All: the page and the object beside it
    v.fit_all()
    vis = _visible(v)
    assert vis.contains(QtCore.QRectF(0, 0, 400, 300)) and vis.contains(r)


def test_fit_all_and_zoom_to_selection_below_the_page(work_page):
    """Fit All and Zoom to Selection below the page."""
    sc = _scene(work_page)
    W, H = work_page.appearance.width, work_page.appearance.height
    v = _view(sc)
    v.fit()
    cell = next(
        it
        for i, it in sc._items.items()
        if work_page.objects[i].parent is None and work_page.objects[i].geometry.y > H
    )
    r = cell.sceneBoundingRect()  # a cell under the page
    assert r.bottom() > H
    v.frame_rect(r, max_zoom=4.0)
    c = _centre(v)
    assert abs(c.x() - r.center().x()) * v.zoom() <= 1.5
    assert abs(c.y() - r.center().y()) * v.zoom() <= 1.5
    v.fit_all()
    vis = _visible(v).adjusted(-1, -1, 1, 1)
    assert vis.contains(QtCore.QRectF(0, 0, W, H).united(sc.itemsBoundingRect()))


# ----------------------------------------------------------- 6. exports ignore it
def _exports(w):
    """Everything a save / print / Convert to Bitmap / the page browser /
    the eye dropper takes from the scene."""
    from ctgedit.ui.browser.preview import render_page_image

    doc, sc = w.doc, w.scene
    W = doc.page.appearance.width or 1024
    H = doc.page.appearance.height or 768
    w._render_thumbnail()
    out = [bytes(doc.parsed.page.thumbnail or b"")]
    img = QtGui.QImage(W, H, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.white)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    with sc.export_only(None):
        sc.render(p, QtCore.QRectF(0, 0, W, H), QtCore.QRectF(0, 0, W, H))
    p.end()
    out.append(img)
    roots = [i for i in doc.page.roots() if not w._unit_locked(i)][:3]
    l, t, r, b = doc.selection_extent(roots, bounds=True)
    bim = QtGui.QImage(max(1, r - l), max(1, b - t), QtGui.QImage.Format_ARGB32)
    bim.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(bim)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    with sc.export_only(doc.expand_to_subtrees(roots)):
        sc.render(
            p, QtCore.QRectF(0, 0, bim.width(), bim.height()), QtCore.QRectF(l, t, bim.width(), bim.height())
        )
    p.end()
    out.append(bim)
    out.append(render_page_image(doc.page, 400, 300))
    # a plain render (tools / diagnostics) keeps the classic look too
    leg = QtGui.QImage(W // 2, H // 2, QtGui.QImage.Format_ARGB32)
    leg.fill(QtGui.QColor("#303030"))
    p = QtGui.QPainter(leg)
    sc.render(p, QtCore.QRectF(0, 0, W // 2, H // 2), QtCore.QRectF(0, 0, W, H))
    p.end()
    out.append(leg)
    out.append(
        [sc.sample_colour(x, y) for x, y in ((1, 1), (W // 2, H // 2), (W - 2, H - 2), (-5, 3), (W + 30, 10))]
    )
    return out


def _quiet(monkeypatch):
    M = QtWidgets.QMessageBox
    for name, answer in (("warning", M.Ok), ("critical", M.Ok), ("information", M.Ok), ("question", M.No)):
        monkeypatch.setattr(M, name, staticmethod(lambda *a, _r=answer, **k: _r))
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self, *a, **k: 0)


def test_exports_ignore_surround(win, tmp_path, monkeypatch, pages):
    from ctgedit.render.scene import SURROUND_MODES
    from ctgedit.selftest import _not_blank

    _quiet(monkeypatch)
    # the loose copies find FxWorkPump's library through the fake install's FxMain
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(pages.work))
    saved = []
    for k, mode in enumerate(("shaded", "workspace")):
        dst = tmp_path / str(k) / "FxWork.ctg"
        dst.parent.mkdir()
        shutil.copyfile(pages.work, dst)
        win.set_surround_mode(mode)
        win.open_document(str(dst))
        _pump()
        if k:
            win.view.set_zoom(3.0)
            win.view.horizontalScrollBar().setValue(win.view.horizontalScrollBar().maximum())
        doc = win.doc
        root = next(
            r
            for r in reversed(doc.page.roots())
            if doc.page.objects[r].geometry.extent_x > 0
            and not win._unit_locked(r)
            and not win._is_template_obj(r)
        )
        doc.move_objects([root], 8, 8)
        win._refresh()
        assert win.save()
        saved.append(dst.read_bytes())
        win.close_current_document()
    # the same records either way (each save has its own identity and stamps)
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg

    stored = []
    for k, data in enumerate(saved):
        p = tmp_path / ("cmp%d.ctg" % k)
        p.write_bytes(data)
        pb = parse_ctg(str(p)).page
        stored.append(([writer.object_payload(o) for o in pb.objects], pb.link_table_raw))
    assert stored[0] == stored[1]

    win.open_document(pages.work)
    _pump()
    first = None
    for mode in SURROUND_MODES:
        win.set_surround_mode(mode)
        for nav in ("fit", "zoom", "scrolled"):
            if nav == "fit":
                win.view.fit()
            elif nav == "zoom":
                win.view.set_zoom(0.1)
            else:
                win.view.set_zoom(2.5)
                win.view.verticalScrollBar().setValue(win.view.verticalScrollBar().maximum())
            _pump()
            win.view.viewport().grab()  # an editor paint in between
            got = _exports(win)
            if first is None:
                first = got
            assert got == first, (mode, nav)
    assert _not_blank(win.scene) > 20
    assert _not_blank(_scene(_page())) == 0


# ----------------------------------------------------- 7. CtDraw's page edge
def test_page_edge_is_ctdraw_focus_rect():
    sc = _scene(_page(w=400, h=300))
    v = _view(sc)
    for zoom in (1.0, 4.0, "fit"):
        if zoom == "fit":
            v.fit()
        else:
            v.set_zoom(zoom)
        v.centerOn(QtCore.QPointF(0, 0) if zoom == 4.0 else QtCore.QPointF(200, 150))
        _pump()
        for mode in ("shaded", "page"):
            sc.set_surround(mode)
            img = v.viewport().grab().toImage()
            tr = v.viewportTransform()
            page_dev = sc._page_device_rect(tr)
            L, T = page_dev.left(), page_dev.top()
            white = QtGui.QColor(255, 255, 255).rgb()
            assert img.pixel(L, T) & 0xFFFFFF == 0xFFFFFF  # the page's own pixel
            assert img.pixel(L + 3, T + 3) == white
            if zoom != 4.0:
                R, B = page_dev.right(), page_dev.bottom()
                assert img.pixel(R, B) == white and img.pixel(R - 3, B - 3) == white
            # the dotted ring on the pixels just OUTSIDE the page: dot / gap
            row = [img.pixel(x, T - 1) for x in range(L + 2, L + 14)]
            col = [img.pixel(L - 1, y) for y in range(T + 2, T + 14)]
            for run in (row, col):
                assert all(run[k] != run[k + 1] for k in range(len(run) - 1)), run
                assert all(run[k] == run[k + 2] for k in range(len(run) - 2)), run
            # one pixel wide at every zoom; beyond it the surround
            surround = sc.surround_colour().rgb()
            assert img.pixel(L - 2, T + 5) == surround
            assert img.pixel(L - 5, T + 5) == surround
            if mode == "page":
                assert surround == white
            else:
                assert surround != white


# ---------------------------------------------------------- 8. grid outside the page
def test_grid_outside_page():
    sc = _scene(_page(w=400, h=300), grid_size=8)
    v = _view(sc)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(400, 300))
    _pump()
    x = v.mapFromScene(QtCore.QPointF(400 + 16, 0)).x()
    y = v.mapFromScene(QtCore.QPointF(0, 332)).y()  # between the lines at 328 / 336
    # the default (the desk; or Grid outside the page off): no grid around the page
    img = v.viewport().grab().toImage()
    surround = sc.surround_colour().rgb()
    assert {img.pixel(x + dx, y) for dx in (-1, 0, 1, 4)} == {surround}
    # Settings > Canvas > Grid outside the page (the Graphics Builder's look)
    sc.set_surround("shaded")
    sc.set_look(grid_outside=True)
    _pump()
    img = v.viewport().grab().toImage()
    surround = sc.surround_colour().rgb()
    cols = [img.pixel(x + dx, y) for dx in (-1, 0, 1)]
    assert any(c != surround for c in cols)  # a grid line out there
    assert img.pixel(x + 4, y) == surround
    inside = v.mapFromScene(QtCore.QPointF(200 + 3, 150 + 3))
    assert img.pixel(inside) & 0xFFFFFF == 0xFFFFFF  # between lines: page
    # no grid (nor surround / edge) in an export
    out = QtGui.QImage(60, 60, QtGui.QImage.Format_ARGB32)
    out.fill(QtGui.QColor(10, 20, 30))
    p = QtGui.QPainter(out)
    with sc.export_only(None):
        sc.render(p, QtCore.QRectF(0, 0, 60, 60), QtCore.QRectF(380, 280, 60, 60))
    p.end()
    assert {out.pixel(x, y) & 0xFFFFFF for x in range(21, 60) for y in range(60)} == {0x0A141E}
    assert {out.pixel(x, y) & 0xFFFFFF for x in range(20) for y in range(20)} == {0xFFFFFF}


# ------------------------------------------------- 9. off-page hit / marquee / draw
def test_hit_marquee_draw_outside_page():
    sc = _scene(_page([_rect(-300, -200, 100, 100), _rect(100, 100, 50, 50)]))
    v = _view(sc)
    v.set_snap(False)
    v.set_smart_guides(False)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(-200, -100))
    _pump()
    at = v.mapFromScene(QtCore.QPointF(-250, -150))
    _mouse(v, QtCore.QEvent.MouseButtonPress, at, LEFT, LEFT)
    _mouse(v, QtCore.QEvent.MouseButtonRelease, at, QtCore.Qt.NoButton, LEFT)
    assert v.selection_roots() == [0]
    v.set_selection([])
    a = v.mapFromScene(QtCore.QPointF(-350, -250))
    b = v.mapFromScene(QtCore.QPointF(-150, -50))
    _mouse(v, QtCore.QEvent.MouseButtonPress, a, LEFT, LEFT)
    _mouse(v, QtCore.QEvent.MouseMove, b, LEFT)
    _mouse(v, QtCore.QEvent.MouseButtonRelease, b, QtCore.Qt.NoButton, LEFT)
    assert v.selection_roots() == [0]
    got = []
    v.drawCommitted.connect(lambda kind, payload: got.append((kind, payload)))
    v.set_tool("rectangle")
    a = v.mapFromScene(QtCore.QPointF(-400, -300))
    b = v.mapFromScene(QtCore.QPointF(-340, -260))
    _mouse(v, QtCore.QEvent.MouseButtonPress, a, LEFT, LEFT)
    _mouse(v, QtCore.QEvent.MouseMove, b, LEFT)
    _mouse(v, QtCore.QEvent.MouseButtonRelease, b, QtCore.Qt.NoButton, LEFT)
    assert got == [("rectangle", {"rect": (-400, -300, -340, -260)})]
    # beyond the record range (only reachable far zoomed out): clamped
    got.clear()
    v.set_zoom(0.02)
    v.centerOn(QtCore.QPointF(31000, 0))
    _pump()
    a = v.mapFromScene(QtCore.QPointF(31500, 100))
    b = v.mapFromScene(QtCore.QPointF(45000, 3000))
    _mouse(v, QtCore.QEvent.MouseButtonPress, a, LEFT, LEFT)
    _mouse(v, QtCore.QEvent.MouseMove, b, LEFT)
    _mouse(v, QtCore.QEvent.MouseButtonRelease, b, QtCore.Qt.NoButton, LEFT)
    ((kind, payload),) = got
    l, t, r, bottom = payload["rect"]
    assert r == 32000 and l < 32000 and all(abs(c) <= 32000 for c in payload["rect"])
    ev = SimpleNamespace(position=lambda: QtCore.QPointF(v.mapFromScene(QtCore.QPointF(45000, -45000))))
    p = v._drop_point(ev)
    assert (p.x(), p.y()) == (32000, -32000)
    assert v._snap_pt(QtCore.QPointF(50000, -50000)) == QtCore.QPointF(32000, -32000)


# ----------------------------------------------------------- 10. tab restore
def test_tab_restore_scroll_in_surround(win, pages):
    win.open_document(pages.work)
    _pump()
    mixer = win.doc
    v = win.view
    v.set_zoom(2.0)
    v.horizontalScrollBar().setValue(v.horizontalScrollBar().maximum())
    v.verticalScrollBar().setValue(v.verticalScrollBar().maximum())
    _pump()
    before = _visible(v)
    assert before.right() > mixer.page.appearance.width + 50  # in the surround
    win.open_document(pages.small)
    _pump()
    assert win.doc is not mixer
    win.set_document(mixer)
    _pump()
    after = _visible(v)
    assert v.zoom() == pytest.approx(2.0)
    assert abs(after.left() - before.left()) <= 1 and abs(after.top() - before.top()) <= 1


# ------------------------------------------------------ 11. the setting and the theme
def test_surround_setting_persists_and_follows_theme(win, monkeypatch):
    from ctgedit.render import scene as SC
    from ctgedit.ui.style import theme
    from ctgedit.ui.prefs.settings import app_settings, surround_mode

    assert surround_mode() == "desk" and win.scene.surround() == "desk"
    assert win.surround_actions["desk"].isChecked()
    win.surround_actions["page"].trigger()
    assert win.scene.surround() == "page"
    assert app_settings().value("view/surround") == "page"
    from ctgedit.ui.main_window import MainWindow

    other = MainWindow()
    try:
        assert other.scene.surround() == "page" and other.surround_actions["page"].isChecked()
    finally:
        MainWindow.instances.remove(other)
        other.deleteLater()
    sc = _scene(_page())
    monkeypatch.setattr(SC.PageScene, "_MARGIN_COLOUR", QtGui.QColor(theme.DARK["desk"]))
    sc.set_surround("workspace")
    assert sc.surround_colour().name() == theme.DARK["desk"]
    sc.set_surround("shaded")
    dark = sc.surround_colour()
    monkeypatch.setattr(SC.PageScene, "_MARGIN_COLOUR", QtGui.QColor(theme.LIGHT["desk"]))
    assert sc.surround_colour() != dark
    sc.set_surround("workspace")
    assert sc.surround_colour().name() == theme.LIGHT["desk"]
    # high contrast: a shaded surround too close to the page falls back
    sc.set_surround("shaded")
    monkeypatch.setattr(SC.PageScene, "_MARGIN_COLOUR", QtGui.QColor("#f0f0f0"))
    assert sc.surround_colour().name() != "#f0f0f0"
    monkeypatch.setitem(theme._state, "scheme", "contrast")
    assert sc.surround_colour().name() == "#f0f0f0"
    # the Settings dialog offers it on the Editing tab
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    assert dlg._surround.currentData() == "page"
    dlg._surround.setCurrentIndex(dlg._surround.findData("workspace"))
    dlg._apply(accept=False)
    assert win.scene.surround() == "workspace" and surround_mode() == "workspace"
    dlg.restore_defaults()
    assert dlg._surround.currentData() == "desk"
    dlg.close()


def test_commands_are_wired(win):
    assert win.act_zoom_all.shortcut().toString() == "Ctrl+3"
    view_menu = next(m for m in win.menuBar().findChildren(QtWidgets.QMenu) if m.title() == "&View")
    assert win.act_zoom_all in view_menu.actions()
    outside = next(m for m in view_menu.findChildren(QtWidgets.QMenu) if m.title() == "Outside the Page")
    assert [a.text() for a in outside.actions()] == [
        "Desk with the page on it",
        "Shaded page colour",
        "Page colour (as the Graphics Builder)",
        "Workspace colour",
    ]
    nav = win.view.findChild(QtWidgets.QFrame, "NavBar")
    assert win.act_zoom_all in nav._zoom_btn.menu().actions()


# -------------------------------------------------- the cached background == direct
def _mirror(src, dst):
    dst.resize(src.size())
    _pump()
    dst.setSceneRect(src.sceneRect())
    dst.setTransform(src.transform())
    dst.horizontalScrollBar().setValue(src.horizontalScrollBar().value())
    dst.verticalScrollBar().setValue(src.verticalScrollBar().value())


def _same(a, b):
    ia, ib = canvas_picture(a), canvas_picture(b)
    assert ia.size() == ib.size()
    bad = [
        (x, y)
        for y in range(0, ia.height(), 2)
        for x in range(0, ia.width(), 2)
        if ia.pixel(x, y) != ib.pixel(x, y)
    ]
    return bad


def test_cached_background_equals_direct_paint(work_page):
    sc = _scene(work_page, grid_size=8)
    cached = _view(sc, size=(1200, 800))
    direct = _view(sc, size=(1200, 800), cache=False)
    assert cached.cacheMode() == QtWidgets.QGraphicsView.CacheBackground
    assert cached.backgroundBrush().style() == QtCore.Qt.NoBrush  # the black-canvas rule

    def check(tag):
        _mirror(cached, direct)
        cached.viewport().grab()  # the cache as it stands
        bad = _same(cached, direct)
        assert not bad, (tag, bad[:5])

    for z in ("fit", 1.0, 4.0):
        if z == "fit":
            cached.fit()
        else:
            cached.set_zoom(z)
        _pump()
        check(z)
        sb = cached.verticalScrollBar()
        for step in (37, 120, -61):
            sb.setValue(sb.value() + step)  # the cache scrolls + fills strips
            hb = cached.horizontalScrollBar()
            hb.setValue(hb.value() + step // 2)
            check((z, "scroll", step))
    cached.set_zoom(1.0)
    cached.centerOn(QtCore.QPointF(0, 0))
    check("corner")
    work_page.appearance.background_colour, old = 0x336699, work_page.appearance.background_colour
    try:
        sc.set_page(work_page, grid_size=8)  # page colour
        check("colour")
        work_page.appearance.width = 1000  # page size
        sc.set_page(work_page, grid_size=(16, 12, 4, 3))  # and grid
        check("size + grid")
    finally:
        work_page.appearance.background_colour = old
        work_page.appearance.width = 1366
    for mode in ("page", "workspace", "shaded"):
        sc.set_surround(mode)
        check(mode)
    sc.set_element_mode(True, (100, 100))
    check("element")
    cached.verticalScrollBar().setValue(cached.verticalScrollBar().value() + 13)
    check("element scrolled")
    sc.set_element_mode(False)
    check("page again")


# ------------------------------------------------------------- the speed-ups
def test_unit_rect_cache_equals_measured(win, pages):
    """The unit is FxWork's biggest root, the genie FxWorkPump (five records)."""
    win.open_document(pages.work)
    _pump()
    v, doc = win.view, win.doc
    v.set_snap(False)
    v.set_smart_guides(False)
    root = max(
        (r for r in doc.page.roots() if not win._unit_locked(r)), key=lambda r: len(doc.page.subtree(r))
    )
    win._on_canvas_selection([root])
    v.set_selection([root])
    _pump()
    unit = v._units[0]
    assert len(unit) == 5  # FxWorkPump and its four members

    def same():
        a, b = v._unit_rect(unit), v._measure_unit(unit)
        assert a is not None and b is not None
        for x, y in (
            (a.left(), b.left()),
            (a.top(), b.top()),
            (a.right(), b.right()),
            (a.bottom(), b.bottom()),
        ):
            assert abs(x - y) < 1e-6

    same()
    v.viewport().grab()
    same()
    # a point where this unit is the one a press grabs
    c = next(
        q
        for q in (
            v.mapFromScene(v.scene()._items[i].sceneBoundingRect().center())
            for i in unit
            if i in v.scene()._items
        )
        if v._topmost_unit(q) and min(v._topmost_unit(q)) == root
    )
    _mouse(v, QtCore.QEvent.MouseButtonPress, c, LEFT, LEFT)
    for k in range(1, 6):
        _mouse(v, QtCore.QEvent.MouseMove, c + QtCore.QPoint(9 * k, 4 * k), LEFT)
        same()  # mid-drag: shifted, not re-measured
    _mouse(v, QtCore.QEvent.MouseButtonRelease, c + QtCore.QPoint(45, 20), QtCore.Qt.NoButton, LEFT)
    _pump()
    unit = v._units[0]
    same()  # after the commit's re-sync
    win.undo()
    _pump()
    unit = v._units[0] if v._units else unit
    same()


def test_marquee_and_unit_memo_give_the_same_selection(win, pages):
    win.open_document(pages.work)
    _pump()
    v, doc = win.view, win.doc
    v.fit()
    _pump()
    W, H = doc.page.appearance.width, doc.page.appearance.height
    a = v.mapFromScene(QtCore.QPointF(-30, -30))
    b = v.mapFromScene(QtCore.QPointF(W + 30, H + 30))
    _mouse(v, QtCore.QEvent.MouseButtonPress, a, LEFT, LEFT)
    _mouse(v, QtCore.QEvent.MouseMove, b, LEFT)
    _mouse(v, QtCore.QEvent.MouseButtonRelease, b, QtCore.Qt.NoButton, LEFT)
    got = sorted(v.selection_roots())
    # the reference: every item resolved on its own
    rect = QtCore.QRectF(v.mapToScene(a), v.mapToScene(b)).normalized()
    want = set()
    for i, item in v.scene()._items.items():
        if not item.isVisible() or v._is_template_anchor(i, item):
            continue
        br = item.sceneBoundingRect()
        if br.isNull() or not rect.contains(br):
            continue
        root = doc.root_of(i)
        if not win._unit_locked(root):
            want.add(root)
    assert got == sorted(want) and got
    # the memo follows a lock at once (Edit > Lock changes no derive)
    r = got[0]
    assert win._unit_for(r) == doc.subtree(r)
    doc.set_locked([r], True)
    assert win._unit_for(r) == []
    doc.set_locked([r], False)
    assert win._unit_for(r) == doc.subtree(r)
    unit = win._unit_for(r)
    unit.append(-1)  # a copy: the memo is untouched
    assert win._unit_for(r) == doc.subtree(r)


def test_element_mode_and_hidden_passes_skip_when_unchanged(monkeypatch):
    sc = _scene(_page([_rect(10, 10, 20, 20), _rect(50, 50, 20, 20)]))
    calls = []
    orig = sc._collect_pane_labels
    monkeypatch.setattr(sc, "_collect_pane_labels", lambda p: (calls.append(p), orig(p)))
    sc.set_element_mode(False)
    assert calls == []  # nothing changed
    sc.set_element_mode(True, (5, 5))
    sc.set_element_mode(True, (5, 5))
    assert len(calls) == 1
    sc.set_element_mode(True, (6, 5))  # the hot spot moved
    assert len(calls) == 2
    sc.set_hidden({1})
    assert not sc._items[1].isVisible() and sc._items[0].isVisible()
    sc.set_hidden(set())
    assert sc._items[1].isVisible()
    page = sc._page
    assert sc.update_units(page, [1])
    sc.set_hidden(set())
    assert all(it.isVisible() for it in sc._items.values())


def _slack_rect(view) -> QtCore.QRectF:
    """What sync_scene_rect gives the view now: the work area + half a
    viewport of slack, united with the visible rect."""
    work = view.scene().work_rect()
    z = view.zoom()
    size = view.maximumViewportSize()
    sx, sy = 0.5 * size.width() / z, 0.5 * size.height() / z
    return work.adjusted(-sx, -sy, sx, sy).united(_visible(view))


def _same_rect(a: QtCore.QRectF, b: QtCore.QRectF) -> bool:
    return all(abs(x - y) < 2 for x, y in zip(a.getRect(), b.getRect()))


def test_tab_switch_drops_the_previous_tabs_view_rect(win, pages):
    """A new tab (fit) or a restored tab gets the view rect of ITS page: the
    region the previous tab showed (here FxWork's far corner at 300 %) is
    not kept as empty scroll range on the small page."""
    win.open_document(pages.work)
    _pump()
    mixer = win.doc
    v = win.view
    v.set_zoom(3.0)
    v.horizontalScrollBar().setValue(v.horizontalScrollBar().maximum())
    v.verticalScrollBar().setValue(v.verticalScrollBar().maximum())
    _pump()
    corner = _visible(v)
    win.open_document(pages.small)  # a new tab: Fit page
    _pump()
    pump = win.doc
    assert pump is not mixer
    assert _same_rect(v.sceneRect(), _slack_rect(v)), (v.sceneRect(), _slack_rect(v))
    assert not v.sceneRect().contains(corner.center())
    v.set_zoom(0.5)
    _pump()
    win.set_document(mixer)  # restored: the far corner again
    _pump()
    assert abs(_visible(v).left() - corner.left()) <= 1
    assert _same_rect(v.sceneRect(), _slack_rect(v))
    win.set_document(pump)  # restored at 50 %
    _pump()
    assert _same_rect(v.sceneRect(), _slack_rect(v)), (v.sceneRect(), _slack_rect(v))


def test_zoom_drops_the_cached_background_before_its_repaint():
    """A zoom re-draws the background cache when it happens (zoomChanged), not
    in the next paint: resetting it inside paintEvent schedules one more
    repaint of the whole canvas per zoom step."""
    v = _view(_scene(_page([_rect(10, 10, 20, 20)]), grid_size=8), size=(900, 700))
    v.viewport().grab()
    for step in (lambda: v.zoom_by(1.15, under_mouse=False), lambda: v.set_zoom(2.5), v.reset_zoom, v.fit):
        step()
        t = v.transform()
        assert v._bg_sig is not None
        assert v._bg_sig[1] == (t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy())


def test_node_drag_keeps_every_selected_node_in_the_record_range():
    """Dragging several nodes far zoomed out: the grabbed node snaps within
    +/- 32000 and the delta is cut so the OTHER selected nodes stay there too."""
    v = _view(_scene(_page([_rect(10, 10, 20, 20)])))
    v.set_snap(False)
    v.set_zoom(0.02)
    nodes = [(31990, 0), (31000, 0), (0, 0)]
    v._node_sel = {0, 1}
    v._node_drag = {
        "index": 0,
        "info": SimpleNamespace(nodes=nodes),
        "grab": 1,
        "press": QtCore.QPointF(31000, 0),
        "delta": (0, 0),
        "moved": True,
        "toggle": False,
    }
    v._drag_nodes_to(QtCore.QPointF(36000, -40000), QtCore.QPoint(10, 10))
    dx, dy = v._node_drag["delta"]
    assert dx == 10  # 31990 + 10 = 32000
    assert dy == -32000
    v._node_drag = None
