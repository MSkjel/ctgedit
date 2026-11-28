"""The underlay."""

import math
import os
import shutil
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app, canvas_picture
from timing import check_speed

#: the pages of FxMain the tests copy: the page the underlay goes under, the one underlaid,
#: and one whose name sorts first
UNDER, SOURCE, FIRST = "FxUnderlay", "FxGenies", "!FxSystem"
#: FxUnderlay's size
PAGE_W, PAGE_H = 1366, 768
#: the test's copy of the fake install (set by _fake_install)
_FX = []

LEFT = QtCore.Qt.LeftButton
NOBUTTON = QtCore.Qt.NoButton
NOMOD = QtCore.Qt.NoModifier
#: synthetic objects lie this far below the page, where nothing else is
OY = 2000
COMMANDS = (
    "act_underlay_page",
    "act_underlay_image",
    "act_underlay_version",
    "act_underlay_show",
    "act_underlay_above",
    "act_underlay_move",
    "act_underlay_settings",
    "act_underlay_remove",
)


def _pump(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture(autouse=True)
def _fake_install(fx_install):
    """Every test has its copy of the fake install (the pages it copies come from it)."""
    _FX[:] = [fx_install]
    yield fx_install
    _FX[:] = []


def _src(name):
    """The path of FxMain's page ``name`` in the test's copy of the fake install."""
    return _FX[0].page(name)


def _copy(name, folder):
    """A copy of FxMain's page ``name`` in ``folder`` (same file name)."""
    src = _src(name)
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(str(folder), os.path.basename(src))
    shutil.copyfile(src, dst)
    return dst


def _window(path=None):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    if path:
        w.open_document(path)
    w.resize(1300, 900)
    w.show()
    _pump()
    return w


def _close(w):
    from ctgedit.ui.editor import underlay as U
    from ctgedit.ui.main_window import MainWindow

    U.render_queue().clear()
    for d in list(w._dialogs.values()):
        try:
            d.close()
        except RuntimeError:
            pass
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    # deleted now: a window only hidden stayed for the whole process, and each theme switch
    # restyled them all
    w.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


@pytest.fixture
def lib(fx_install, monkeypatch):
    """The copies outside the install find their libraries through FxMain's include chain."""
    monkeypatch.setenv("CTG_LIB_PATH", fx_install.project("FxMain"))


@pytest.fixture
def fake_render(monkeypatch):
    """Stand in for the render process: a page drawn at 2x as black boxes (``state.boxes``,
    in the source page's pixels) on a transparent picture, delivered from the event loop."""
    from ctgedit.ui.editor import underlay as U

    state = types.SimpleNamespace(calls=[], page=(PAGE_W, PAGE_H), boxes=[[500.0, 300.0, 80.0, 40.0]])

    def request(queue, token, path, w, h, options):
        state.calls.append((path, w, h, dict(options)))
        pw, ph = state.page
        k = 2
        img = QtGui.QImage(pw * k, ph * k, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(img)
        for x, y, bw, bh in state.boxes:
            p.fillRect(QtCore.QRectF(x * k, y * k, bw * k, bh * k), QtGui.QColor(0, 0, 0))
        p.end()
        info = {
            "objects": len(state.boxes),
            "page": [pw, ph],
            "scale": float(k),
            "size": [img.width(), img.height()],
            "boxes": [list(b) for b in state.boxes],
        }
        QtCore.QTimer.singleShot(0, lambda: queue._on_rendered(token, img, info, ""))

    monkeypatch.setattr(U, "request_render", request)
    return state


@pytest.fixture
def mixer(tmp_path, lib):
    w = _window(_copy(UNDER, tmp_path / "page"))
    yield w
    _close(w)


def _picture(path, w=100, h=50, colour=(220, 30, 30)):
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(*colour))
    assert img.save(str(path), "PNG")
    return str(path)


def _underlay_page(win, path, monkeypatch):
    from ctgedit.ui.editor import underlay as U

    monkeypatch.setattr(U, "ask_page", lambda _w, _d: path)
    win.act_underlay_page.trigger()
    assert U.render_queue().wait(120)
    _pump(3)
    return win.underlay()


def _underlay_image(win, path, monkeypatch):
    from ctgedit.ui.editor import underlay as U

    monkeypatch.setattr(U, "ask_image", lambda _w: path)
    win.act_underlay_image.trigger()
    _pump(2)
    return win.underlay()


def _grab(win):
    _pump(2)
    win.view.resetCachedContent()
    return win.view.viewport().grab().toImage()


def _px(win, img, x, y) -> QtGui.QColor:
    p = win.view.mapFromScene(QtCore.QPointF(x, y))
    return img.pixelColor(p.x(), p.y())


def _bare(win):
    """Only the page and the underlay show: no grid, every object hidden."""
    if win.act_grid.isChecked():
        win.act_grid.trigger()
    win.scene.set_hidden(set(win.doc.page.roots()))
    _pump(2)


def _me(view, kind, vp, buttons=NOBUTTON, mods=NOMOD):
    vp = QtCore.QPoint(vp)
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), LEFT, buttons, mods)


def _drag(view, a, b, steps=6, through_filters=False):
    """A left drag on the canvas from viewport point ``a`` to ``b`` (``through_filters``: sent
    as events, so the viewport's event filters see them)."""
    a, b = QtCore.QPoint(a), QtCore.QPoint(b)
    vp = view.viewport()

    def send(kind, at, buttons):
        ev = _me(view, kind, at, buttons)
        if through_filters:
            QtWidgets.QApplication.sendEvent(vp, ev)
        elif kind == QtCore.QEvent.MouseButtonPress:
            view.mousePressEvent(ev)
        elif kind == QtCore.QEvent.MouseMove:
            view.mouseMoveEvent(ev)
        else:
            view.mouseReleaseEvent(ev)

    send(QtCore.QEvent.MouseButtonPress, a, LEFT)
    for k in range(1, steps + 1):
        send(QtCore.QEvent.MouseMove, a + (b - a) * (k / steps), LEFT)
    send(QtCore.QEvent.MouseButtonRelease, b, NOBUTTON)
    _pump(2)


def _sp(view, x, y):
    return view.mapFromScene(QtCore.QPointF(x, y))


# ------------------------------------------------------------------------- the commands
def test_the_commands_sit_in_view_underlay_with_ids_icons_and_sentences(lib):
    from ctgedit.ui.main_window_base import DESCRIPTIONS
    from ctgedit.ui.dialog.palette import harvest_actions

    w = _window()
    try:
        rows = {a.objectName(): crumb for a, crumb, _l in harvest_actions(w.menuBar())}
        for name in COMMANDS:
            a = getattr(w, name)
            assert a.objectName() == name
            assert rows.get(name) == "View > Underlay", (name, rows.get(name))
            assert not a.icon().isNull(), name
            assert DESCRIPTIONS.get(name, "").endswith("."), name
            # no page: every command is greyed (and says what it needs)
            assert not a.isEnabled(), name
        assert w.act_underlay_show.isCheckable() and w.act_underlay_above.isCheckable()
        assert w.act_underlay_show.shortcut().toString() == "Alt+Shift+U"
        # no other command has that key
        owners = [
            a for a in w.findChildren(QtGui.QAction) if "Alt+Shift+U" in [s.toString() for s in a.shortcuts()]
        ]
        assert owners == [w.act_underlay_show]
        # the command palette lists them by itself
        from ctgedit.ui.dialog import palette

        names = {r[0].objectName() for r in palette.harvest_actions(w.menuBar())}
        assert set(COMMANDS) <= names
    finally:
        _close(w)


def test_with_a_page_the_source_commands_are_on_and_the_others_need_an_underlay(mixer):
    w = mixer
    w._update_actions()
    for name in ("act_underlay_page", "act_underlay_image", "act_underlay_settings"):
        assert getattr(w, name).isEnabled(), name
    for name in (
        "act_underlay_show",
        "act_underlay_above",
        "act_underlay_remove",
        "act_underlay_version",
    ):  # (no earlier versions yet either)
        assert not getattr(w, name).isEnabled(), name
    assert not w.status_underlay.isVisible()


# ----------------------------------------------------------------------------- drawing
def test_a_page_underlay_is_drawn_by_the_render_process_faded_and_lined_up(tmp_path, mixer, monkeypatch):
    """A real render: the editor's process never reads the source page (the reader is not
    called), its objects lie exactly where they are on that page, faded and grey."""
    from ctgedit.io import reader
    from ctgedit.ui.editor import underlay as U

    w = mixer
    src = _copy(UNDER, tmp_path / "source")
    calls = []
    real = reader.read_page
    monkeypatch.setattr(reader, "read_page", lambda p, *a, **k: calls.append(p) or real(p))
    _bare(w)
    before = _grab(w)
    ul = _underlay_page(w, src, monkeypatch)
    assert ul is not None and ul.status == "ready", (ul.status, ul.error)
    assert ul.kind == "page" and (ul.x, ul.y, ul.scale) == (0.0, 0.0, 1.0)
    assert not calls, "the editor's process read the page"
    pw, ph = w.doc.page.appearance.width, w.doc.page.appearance.height
    assert ul.base == (float(pw), float(ph))
    # the picture is 2x the page (Settings > Canvas > Underlay), the template left out
    pic_w, pic_h = ul.pic_size
    assert abs(pic_w - 2 * pw) <= 2 and abs(pic_h - 2 * ph) <= 2
    # one full picture kept: with the Grey tint the picture as it came went (its grey values
    # and the tinted copy stay)
    assert ul.image is None and ul.tint == "grey"
    # the boxes are the page's own top-level objects (the same page): lined up at 0, 0
    page_boxes = []
    for i in w.doc.page.roots():
        g = w.doc.page.objects[i].geometry
        if g is not None and (g.extent_x > 0 or g.extent_y > 0):
            page_boxes.append((float(g.x), float(g.y), float(max(0, g.extent_x)), float(max(0, g.extent_y))))
    assert set(ul.boxes) <= set(page_boxes) and len(ul.boxes) >= 20
    # placed 3, 2 px off a reference object's corner, a symbol lands on that corner (on this
    # busy page the nearest left or top edge is often another object's, far away)
    zoom = w.view.zoom()
    w.view.set_zoom(1.0)
    corners = sorted({(math.floor(b[0] + 0.5), math.floor(b[1] + 0.5)) for b in ul.boxes})
    lone = [
        c
        for c in corners
        if not any(o != c and max(abs(o[0] - c[0] - 3), abs(o[1] - c[1] - 2)) <= 6 for o in corners)
    ]
    assert len(lone) >= 10, len(lone)
    off = [c for c in lone if w.view.placement_point(c[0] + 3, c[1] + 2) != c]
    assert not off, off
    w.view.set_zoom(zoom)
    after = _grab(w)
    # where the reference has an opaque pixel the canvas shows it faded (40 %) and grey,
    # at the same page point; where it has none, the canvas is as it was
    k = pic_w / float(pw)
    tinted = ul.tinted()

    def ink(x, y):
        return tinted.pixelColor(int(x * k), int(y * k))

    inked = []
    for bx, by, bw, bh in ul.boxes:
        cx, cy = bx + bw / 2.0, by + bh / 2.0
        c = ink(cx, cy)
        if (
            bw >= 20
            and bh >= 20
            and c.alpha() == 255
            and all(ink(cx + dx, cy + dy) == c for dx in (-2, 2) for dy in (-2, 2))
        ):
            inked.append((cx, cy, c))
    assert len(inked) >= 5, len(inked)
    for cx, cy, c in inked:
        assert c.red() == c.green() == c.blue()  # grey
        a, b = _px(w, before, cx, cy), _px(w, after, cx, cy)
        for ca, cb, cc in (
            (a.red(), b.red(), c.red()),
            (a.green(), b.green(), c.green()),
            (a.blue(), b.blue(), c.blue()),
        ):
            assert abs(cb - (ca * 0.6 + cc * 0.4)) <= 3, (cx, cy, a.name(), b.name(), c.name())
    empty = next(
        (x, y)
        for x in range(20, int(pw) - 20, 37)
        for y in range(20, int(ph) - 20, 29)
        if all(ink(x + dx, y + dy).alpha() == 0 for dx in (-3, 0, 3) for dy in (-3, 0, 3))
    )
    assert _px(w, after, *empty) == _px(w, before, *empty)
    assert w.status_underlay.isVisible() and w.status_underlay.text() == "Underlay 40 %"
    assert UNDER in w.status_underlay.toolTip() and src in w.status_underlay.toolTip()
    # the same file again comes from the cache (no second render)
    w.reload_underlay()
    assert w.underlay().status == "ready" and not U.render_queue().busy_with(ul)


def test_the_render_options_and_the_template(tmp_path, mixer, fake_render, monkeypatch):
    from ctgedit.ui.editor import underlay as U

    w = mixer
    src = _copy(SOURCE, tmp_path / "source")
    ul = _underlay_page(w, src, monkeypatch)
    assert ul.status == "ready" and ul.boxes == [(500.0, 300.0, 80.0, 40.0)]
    assert not w.statusBar().currentMessage().startswith("Drawing")  # (the note went)
    path, mw, mh, opts = fake_render.calls[-1]
    assert path == os.path.abspath(src)
    assert opts == {
        "transparent": True,
        "boxes": True,
        "fast": True,
        "template": False,
        "factor": 2,
        "max_pixels": U.MAX_PIXELS,
    }
    sw, sh = U.source_page_size(src)  # (from the page's head: no parse)
    assert (mw, mh) == (2 * sw, 2 * sh)  # the source page's size times 2
    # Draw its template: drawn again with it
    w.act_underlay_settings.trigger()
    _pump(2)
    dlg = w._dialogs.get("underlay")
    dlg.cb_template.setChecked(True)
    from ctgedit.ui.editor import underlay as U

    assert U.render_queue().wait(30)
    assert fake_render.calls[-1][3]["template"] is True and w.underlay().template


def test_an_image_underlay_with_offset_scale_opacity_tint_and_above(tmp_path, mixer, monkeypatch):
    w = mixer
    v = w.view
    # an opaque object below the page, and the picture over half of it
    i = w.doc.create_object("rectangle", (300, OY + 300, 500, OY + 400), None, colour=0x00CC00)
    w._refresh()
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(400, OY + 350))
    if w.act_grid.isChecked():
        w.act_grid.trigger()
    before = _grab(w)
    pic = _picture(tmp_path / "ref.png", 100, 50, (220, 30, 30))
    ul = _underlay_image(w, pic, monkeypatch)
    assert ul.kind == "image" and ul.status == "ready" and ul.base == (100.0, 50.0)
    assert (ul.x, ul.y, ul.scale) == (0.0, 0.0, 1.0)  # one picture px = one page px
    # the window places it: X / Y / Scale, live
    w.act_underlay_settings.trigger()
    _pump(2)
    dlg = w._dialogs.get("underlay")
    assert dlg is not None and dlg.isVisible() and not dlg.isModal()
    dlg.sp_x.setValue(250)
    dlg.sp_y.setValue(OY + 250)
    dlg.sp_scale.setValue(200.0)
    assert ul.rect() == QtCore.QRectF(250, OY + 250, 200, 100)
    # tint None at 100 %: its own colour off the object; under the object, the object stays
    dlg.cb_tint.setCurrentIndex(dlg.cb_tint.findData("none"))
    dlg.sp_opacity.setValue(100)
    assert ul.tint == "none" and ul.opacity == 1.0
    img = _grab(w)
    c = _px(w, img, 270, OY + 270)  # the picture, no object there
    assert c.red() > 200 and c.green() < 60 and c.blue() < 60, c.name()
    assert _px(w, img, 400, OY + 330) == _px(w, before, 400, OY + 330)  # under the object
    # above the objects: it covers the object
    w.act_underlay_above.trigger()
    assert ul.above and w.act_underlay_above.isChecked() and dlg.cb_above.isChecked()
    img = _grab(w)
    c = _px(w, img, 400, OY + 330)
    assert c.red() > 200 and c.green() < 60, c.name()
    # 40 % grey: a faded grey, between the object's green and the picture
    dlg.sp_opacity.setValue(40)
    dlg.cb_tint.setCurrentIndex(dlg.cb_tint.findData("grey"))
    img = _grab(w)
    c = _px(w, img, 400, OY + 330)
    o = _px(w, before, 400, OY + 330)
    assert c != o and c.green() < o.green() and c.red() < 160, (c.name(), o.name())
    # blue: more blue than red
    dlg.cb_tint.setCurrentIndex(dlg.cb_tint.findData("blue"))
    img = _grab(w)
    c = _px(w, img, 270, OY + 270)
    assert c.blue() > c.red(), c.name()
    # Fit Page: as large as fits the page, centred; Actual Size: 100 % at 0, 0
    dlg.btn_fit.click()
    pw, ph = w.doc.page.appearance.width, w.doc.page.appearance.height
    r = ul.rect()
    assert abs(r.width() - pw) < 0.5 or abs(r.height() - ph) < 0.5
    assert abs(r.center().x() - pw / 2) < 1 and abs(r.center().y() - ph / 2) < 1
    dlg.btn_actual.click()
    assert (ul.x, ul.y, ul.scale) == (0.0, 0.0, 1.0)
    # Show off: nothing drawn
    w.act_underlay_show.trigger()
    assert not ul.shown and not dlg.cb_show.isChecked()
    assert w.status_underlay.text() == "Underlay hidden"
    del i


def test_a_save_print_export_and_convert_to_bitmap_are_the_same_with_an_underlay(tmp_path, lib, monkeypatch):
    """Editor chrome only: the bytes a save writes (its thumbnail included), the page
    exported as a picture and a Convert to Bitmap are the same with an underlay shown (above
    the objects, 100 %, its own colours) as without."""
    import uuid
    from ctgedit.format import element as E

    # (a save gives the page a new identity, a time and a GUID: the same for both here)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0x1234))
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda *_a, **_k: 0x5A5A5A5A)
    a = _copy(FIRST, tmp_path / "a")
    b = _copy(FIRST, tmp_path / "b")
    pic = _picture(tmp_path / "ref.png", 400, 300, (255, 0, 255))
    w = _window(a)
    try:
        w.open_document(b)
        docs = [e["doc"] for e in w._docs]
        assert len(docs) == 2
        out = {}
        for k, doc in enumerate(docs):
            w.set_document(doc)
            _pump(2)
            if k == 0:
                ul = _underlay_image(w, pic, monkeypatch)
                ul.opacity, ul.tint, ul.above = 1.0, "none", True
                ul.touch()
                w.underlay_changed()
                assert w.underlay() is ul and ul.shown
                # (it is drawn on the canvas)
                img = _grab(w)
                c = _px(w, img, 50, 50)
                assert (c.red(), c.green(), c.blue()) == (255, 0, 255), c.name()
            else:
                assert w.underlay() is None
            i = doc.create_object("rectangle", (40, 40, 140, 90), None, colour=0x3366CC)
            w._refresh()
            assert w.save()
            with open(doc.path, "rb") as fh:
                saved = fh.read()
            png = str(tmp_path / ("export%d.png" % k))
            w._render_page_to(
                png, ".png", int(doc.page.appearance.width), int(doc.page.appearance.height), 1.0
            )
            with open(png, "rb") as fh:
                exported = fh.read()
            w._set_selection([i])
            assert w.act_to_bitmap.isEnabled()
            ser0 = doc.serialize()
            w.act_to_bitmap.trigger()
            _pump(1)
            assert doc.serialize() != ser0  # (it was converted)
            out[k] = (saved, exported, doc.serialize())
        assert out[0][0] == out[1][0], "the saved bytes differ"
        assert out[0][1] == out[1][1], "the exported page differs"
        assert out[0][2] == out[1][2], "Convert to Bitmap differs"
    finally:
        _close(w)


# ----------------------------------------------------------------------------- snapping
def test_a_dragged_object_snaps_to_an_underlay_objects_edge(tmp_path, mixer, fake_render, monkeypatch):
    w, v = mixer, mixer.view
    # the reference's object at (500, 100) of its page; the underlay lies at (2500, OY), so
    # the box is at (3000, OY + 100) of this page, far from the page's own objects
    fake_render.boxes = [[500.0, 100.0, 80.0, 40.0]]
    ul = _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
    assert ul.status == "ready"
    w.move_underlay_to(2500, OY)
    assert ul.snap_rects() == [QtCore.QRectF(3000, OY + 100, 80, 40)]
    i = w.doc.create_object("rectangle", (2800, OY + 200, 2860, OY + 240), None, colour=0x3366CC)
    w._refresh()
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(2950, OY + 200))
    v.set_snap(False)
    v.set_smart_guides(False)  # (the underlay snaps all the same)
    _pump(2)
    w._set_selection([i])
    v.select_units([[i]], reveal=False)
    # drag the rectangle's left edge (2800) to 3 px short of the box's left edge (3000)
    _drag(v, _sp(v, 2830, OY + 220), _sp(v, 3027, OY + 220))
    g = w.doc.page.objects[i].geometry
    assert (g.x, g.y) == (3000, OY + 200), (g.x, g.y)
    # its snapping can be turned off in the window: then it lands where it was let go
    w.act_underlay_settings.trigger()
    _pump(2)
    w._dialogs.get("underlay").cb_snap.setChecked(False)
    assert not ul.snap
    w._set_selection([i])
    v.select_units([[i]], reveal=False)
    _drag(v, _sp(v, 3030, OY + 220), _sp(v, 3127, OY + 220))
    assert w.doc.page.objects[i].geometry.x == 3097
    # snapping on and the smart guides on: the box is a target, with its guide line and
    # its outline while the drag runs
    w._dialogs.get("underlay").cb_snap.setChecked(True)
    assert ul.snap
    v.set_smart_guides(True)
    w._set_selection([i])
    v.select_units([[i]], reveal=False)
    a, b = _sp(v, 3127, OY + 220), _sp(v, 3032, OY + 220)
    v.mousePressEvent(_me(v, QtCore.QEvent.MouseButtonPress, a, LEFT))
    for k in range(1, 6):
        v.mouseMoveEvent(_me(v, QtCore.QEvent.MouseMove, a + (b - a) * (k / 5), LEFT))
    assert ("v", 3000.0) in v._sg_active, v._sg_active
    assert QtCore.QRectF(3000, OY + 100, 80, 40) in v.smart_guide_partners()
    v.mouseReleaseEvent(_me(v, QtCore.QEvent.MouseButtonRelease, b))
    _pump(1)
    assert w.doc.page.objects[i].geometry.x == 3000
    # a point drawn or a handle dragged near the box's edge snaps to it as to a guideline
    assert v._snap_pt(QtCore.QPointF(3003.0, OY + 50.0)).x() == 3000.0
    w.act_underlay_show.trigger()  # hidden: nothing to snap to
    assert v._snap_pt(QtCore.QPointF(3003.0, OY + 50.0)).x() == 3003.0


# ------------------------------------------------------------------- tabs and memory
def test_each_tab_has_its_own_underlay_and_the_page_remembers_it(tmp_path, lib, monkeypatch):
    from ctgedit.ui.editor import underlay as U

    a = _copy(UNDER, tmp_path / "a")
    b = _copy(FIRST, tmp_path / "b")
    pic = _picture(tmp_path / "ref.png")
    w = _window(a)
    try:
        ul = _underlay_image(w, pic, monkeypatch)
        ul.x, ul.y, ul.opacity = 12.0, 34.0, 0.55
        ul.touch()
        w.underlay_changed()
        doc_a = w.doc
        w.open_document(b)
        _pump(2)
        assert w.doc is not doc_a and w.underlay() is None
        assert not w.act_underlay_show.isEnabled() and not w.status_underlay.isVisible()
        w.set_document(doc_a)
        _pump(2)
        assert w.underlay() is ul and w.act_underlay_show.isChecked()
        assert w.status_underlay.isVisible() and w.status_underlay.text() == "Underlay 55 %"
        # the page's underlay is kept in the settings by its path
        kept = U.remembered(doc_a)
        assert kept is not None and kept.path == os.path.abspath(pic)
        assert (kept.x, kept.y, kept.opacity) == (12.0, 34.0, 0.55)
    finally:
        _close(w)
    # the page opens again (another window): its underlay is back where it was left
    w2 = _window(a)
    try:
        w2._update_actions()
        _pump(2)
        ul2 = w2.underlay()
        assert ul2 is not None and ul2.status == "ready"
        assert (ul2.x, ul2.y, ul2.opacity) == (12.0, 34.0, 0.55)
        # off in Settings: nothing comes back
        from ctgedit.ui.prefs.settings import set_option

        set_option("underlay_remember", False)
    finally:
        _close(w2)
    w3 = _window(a)
    try:
        assert w3.underlay() is None
    finally:
        _close(w3)


def test_a_source_that_is_gone_is_dropped_with_one_notice(tmp_path, lib, monkeypatch):
    from ctgedit.ui.editor import underlay as U
    from ctgedit.ui.notify import centre

    a = _copy(UNDER, tmp_path / "a")
    pic = _picture(tmp_path / "gone.png")
    w = _window(a)
    try:
        _underlay_image(w, pic, monkeypatch)
        assert U.remembered(w.doc) is not None
    finally:
        _close(w)
    os.remove(pic)
    w = _window(a)
    try:
        for _ in range(3):
            w._update_actions()
        _pump(2)
        assert w.underlay() is None
        msgs = [m for m in centre(w).entries if "gone.png" in m.text]
        assert len(msgs) == 1 and msgs[0].level == "warning", [m.text for m in msgs]
        assert U.remembered(w.doc) is None  # (forgotten)
    finally:
        _close(w)


def test_remove_leaves_no_overlay_and_forgets_it(tmp_path, mixer, monkeypatch):
    from ctgedit.ui.editor import underlay as U

    w = mixer
    _bare(w)
    before = _grab(w)
    pic = _picture(tmp_path / "ref.png", 300, 200, (0, 0, 255))
    ul = _underlay_image(w, pic, monkeypatch)
    ul.opacity = 1.0
    ul.touch()
    w.underlay_changed()
    assert _grab(w) != before
    w.act_underlay_remove.trigger()
    assert w.underlay() is None and U.remembered(w.doc) is None
    assert _grab(w) == before
    assert not w.status_underlay.isVisible()
    assert not w.act_underlay_show.isEnabled() and not w.act_underlay_remove.isEnabled()


# ------------------------------------------------------------- the status field, the menu
def test_the_status_field_toggles_with_a_click_and_opens_the_window_with_a_double_click(
    tmp_path, mixer, monkeypatch
):
    w = mixer
    ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    f = w.status_underlay
    assert f.isVisible() and f.text() == "Underlay 40 %"
    assert "ref.png" in f.toolTip()
    QtTest.QTest.mouseClick(f, LEFT)
    QtTest.QTest.qWait(QtWidgets.QApplication.doubleClickInterval() + 80)
    assert not ul.shown and f.text() == "Underlay hidden"
    QtTest.QTest.mouseClick(f, LEFT)
    QtTest.QTest.qWait(QtWidgets.QApplication.doubleClickInterval() + 80)
    assert ul.shown
    QtTest.QTest.mouseDClick(f, LEFT)
    # (Qt 6.11's QTest.mouseDClick leaves the button held for the application: a later
    # test's mouseButtons() check would see it pressed)
    QtTest.QTest.mouseRelease(f, LEFT)
    assert QtWidgets.QApplication.mouseButtons() == NOBUTTON
    QtTest.QTest.qWait(QtWidgets.QApplication.doubleClickInterval() + 80)
    assert ul.shown  # (a double-click does not toggle)
    dlg = w._dialogs.get("underlay")
    assert dlg is not None and dlg.isVisible()


def test_the_canvas_menu_has_an_underlay_submenu_while_the_page_has_one(tmp_path, mixer, monkeypatch):
    from ctgedit.ui.window import context_menu

    w = mixer
    pos = w.view.viewport().mapToGlobal(QtCore.QPoint(50, 50))

    def sub(menu):
        return next((a for a in menu.actions() if a.text() == "Underlay"), None)

    m = context_menu.build_canvas_menu(w, pos, [])
    assert sub(m) is None
    m.deleteLater()
    _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    m = context_menu.build_canvas_menu(w, pos, [])
    a = sub(m)
    assert a is not None
    inner = [x for x in m.findChildren(QtWidgets.QMenu) if x.title() == "Underlay"][0]
    assert [x.objectName() for x in inner.actions()] == [
        "act_underlay_show",
        "act_underlay_above",
        "act_underlay_move",
        "act_underlay_settings",
        "act_underlay_remove",
    ]
    m.deleteLater()


# ---------------------------------------------------------------- moving with the mouse
def test_move_with_the_mouse_drags_the_underlay_not_the_objects(tmp_path, mixer, monkeypatch):
    w, v = mixer, mixer.view
    ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(400, 300))
    w.act_underlay_settings.trigger()
    _pump(2)
    dlg = w._dialogs.get("underlay")
    serial = w.doc.edit_serial if hasattr(w.doc, "edit_serial") else None
    dirty = w.doc.dirty
    dlg.btn_move.click()
    assert ul.moving and dlg.move_note.isVisible() and dlg.btn_move.isChecked()
    _drag(v, _sp(v, 300, 300), _sp(v, 340, 330), through_filters=True)
    assert (ul.x, ul.y) == (40.0, 30.0)
    assert w.doc.dirty == dirty  # (no object moved)
    if serial is not None:
        assert w.doc.edit_serial == serial
    # the arrow keys nudge it by the editor's steps: the
    # arrow-key move step (1 px), Shift the large step (10 steps)
    v.setFocus()
    QtTest.QTest.keyClick(v, QtCore.Qt.Key_Right)
    QtTest.QTest.keyClick(v, QtCore.Qt.Key_Down, QtCore.Qt.ShiftModifier)
    assert (ul.x, ul.y) == (41.0, 40.0)
    assert w.doc.dirty == dirty
    from ctgedit.ui.prefs.settings import app_settings

    w._nudge_step, w._big_nudge = 8, 5  # (Settings > Editing, as the window keeps them)
    QtTest.QTest.keyClick(v, QtCore.Qt.Key_Right)
    QtTest.QTest.keyClick(v, QtCore.Qt.Key_Down, QtCore.Qt.ShiftModifier)
    assert (ul.x, ul.y) == (49.0, 80.0)
    app_settings().setValue("opt/ctrl_arrow", "grid")
    gx, gy, _ox, _oy = v.grid()
    QtTest.QTest.keyClick(v, QtCore.Qt.Key_Right, QtCore.Qt.ControlModifier)
    assert (ul.x, ul.y) == (49.0 + gx, 80.0)
    app_settings().setValue("opt/ctrl_arrow", "x10")
    QtTest.QTest.keyClick(v, QtCore.Qt.Key_Up, QtCore.Qt.ControlModifier)
    assert (ul.x, ul.y) == (49.0 + gx, 40.0)
    app_settings().remove("opt/ctrl_arrow")
    w._nudge_step, w._big_nudge = 1, 10
    # Esc puts it back where it was when moving began, and the mouse moves objects again
    QtTest.QTest.keyClick(v, QtCore.Qt.Key_Escape)
    assert (ul.x, ul.y) == (0.0, 0.0) and not ul.moving
    assert not dlg.btn_move.isChecked()
    # (remembered where it is)
    from ctgedit.ui.editor import underlay as U

    kept = U.remembered(w.doc)
    assert (kept.x, kept.y) == (0.0, 0.0)


# ------------------------------------------------------------------ the earlier version
def test_an_earlier_version_is_underlaid_with_its_page(tmp_path, mixer, fake_render, monkeypatch):
    from ctgedit.infra import history as H
    from ctgedit.ui.editor import underlay as U

    monkeypatch.setenv("CTG_DATA_DIR", str(tmp_path / "data"))
    H.configure(10, 200)
    w = mixer
    doc = w.doc
    r = doc.page.roots()[0]
    doc.move_objects([r], 4, 0)
    assert w.save()
    w._update_actions()
    versions = H.list_versions(doc.path)
    assert versions and w.act_underlay_version.isEnabled()
    picked = []
    monkeypatch.setattr(U, "ask_version", lambda _w, _d, vs: picked.append(vs) or vs[-1])
    w.act_underlay_version.trigger()
    assert U.render_queue().wait(30)
    _pump(2)
    ul = w.underlay()
    assert ul.kind == "version" and ul.path == versions[-1].file
    assert ul.page_path == os.path.abspath(doc.path) and ul.status == "ready"
    assert fake_render.calls[-1][3]["as_path"] == os.path.abspath(doc.path)
    assert ul.when in ul.name() and picked and len(picked[0]) == len(versions)
    # File > Page History's Underlay does the same for the version picked there
    w.act_underlay_remove.trigger()
    w.act_page_history.trigger()
    _pump(2)
    hist = w._modeless_for("pagehistory")
    assert hist.btn_underlay.isEnabled()
    hist.btn_underlay.click()
    assert U.render_queue().wait(30)
    _pump(2)
    assert w.underlay().kind == "version" and w.underlay().path == hist.current().file
    hist.close()


def test_the_page_picker_says_underlay_and_offers_no_delete(lib):
    """Underlay a Page…: the page browser, Pages only, its default button 'Underlay'; a
    picker deletes nothing (no Delete button, menu entry or key)."""
    _app()
    from ctgedit.ui.browser.dialog import MODE_OPEN, PageBrowserDialog

    dlg = PageBrowserDialog(
        MODE_OPEN, None, start_path=_src(UNDER), kinds=("page",), title="Underlay a Page", ok_text="Underlay"
    )
    try:
        assert dlg.windowTitle() == "Underlay a Page"
        assert dlg.ok_button.text() == "Underlay"
        assert dlg.btn_delete is not None and dlg.btn_delete.isHidden()
    finally:
        dlg.deleteLater()
    plain = PageBrowserDialog(MODE_OPEN, None, start_path=_src(UNDER), kinds=("page",))
    try:
        assert plain.ok_button.text() == "Open" and not plain.btn_delete.isHidden()
    finally:
        plain.deleteLater()


def test_the_render_process_draws_a_version_with_its_page_and_lists_the_boxes(tmp_path, lib):
    """preview.render_to_files' underlay options, in this process: a transparent picture, the
    page's top-level boxes, a version drawn as its page (as_path), no template."""
    from ctgedit.ui.browser import preview

    _app()
    page = _copy(UNDER, tmp_path / "page")
    version = str(tmp_path / "v.ctg")
    shutil.copyfile(page, version)
    out = str(tmp_path / "out")
    # (all the room there is, but at most 1 picture pixel a page pixel)
    res = preview.render_to_files(
        version,
        out,
        8192,
        8192,
        "literal",
        {"transparent": True, "boxes": True, "fast": True, "template": False, "as_path": page, "factor": 1},
    )
    assert "error" not in res, res
    assert res["page"] == [PAGE_W, PAGE_H] and res["scale"] == 1.0 and len(res["boxes"]) > 20
    assert res["size"] == [PAGE_W, PAGE_H]
    img = QtGui.QImage(out + ".png")
    assert img.hasAlphaChannel() and img.pixelColor(2, 2).alpha() == 0  # (no page colour)
    with_tpl = preview.render_to_files(
        version,
        out + "t",
        PAGE_W,
        PAGE_H,
        "literal",
        {"transparent": True, "template": True, "as_path": page},
    )
    tpl = QtGui.QImage(out + "t.png")
    # the pixel cap: 4x asked, at most the page's own pixels given
    capped = preview.render_to_files(
        version,
        out + "c",
        8192,
        8192,
        "literal",
        {"transparent": True, "as_path": page, "factor": 4, "max_pixels": PAGE_W * PAGE_H},
    )
    assert capped["size"][0] * capped["size"][1] <= PAGE_W * PAGE_H
    # the template (its title bar: fx_hd normal@HD768 draws it 30 px high) is drawn only
    # when asked
    assert "error" not in with_tpl
    assert tpl.pixelColor(40, 15).alpha() > 0 and img.pixelColor(40, 15).alpha() == 0


# -------------------------------------------------------------------- settings, themes
def test_the_settings_rows_and_what_a_new_underlay_starts_with(tmp_path, mixer, monkeypatch):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    w = mixer
    dlg = SettingsDialog(w)
    try:
        page = dlg._tabs.widget(dlg._tabs.indexOf("Canvas"))
        keys = {r.key for r in page.rows()}
        for key in (
            "opt/underlay_opacity",
            "opt/underlay_tint",
            "opt/underlay_above",
            "opt/underlay_snap",
            "opt/underlay_remember",
            "opt/underlay_resolution",
        ):
            assert key in keys, key
        assert dlg._row_widgets["opt/underlay_opacity"].value() == 40
        assert dlg._row_widgets["opt/underlay_tint"].currentData() == "grey"
        assert dlg._row_widgets["opt/underlay_resolution"].currentData() == 2
        dlg._row_widgets["opt/underlay_opacity"].setValue(65)
        dlg._row_widgets["opt/underlay_tint"].setCurrentIndex(
            dlg._row_widgets["opt/underlay_tint"].findData("blue")
        )
        dlg._row_widgets["opt/underlay_above"].setChecked(True)
        dlg._store()
    finally:
        dlg.close()
        dlg.deleteLater()
    ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    assert (round(ul.opacity * 100), ul.tint, ul.above) == (65, "blue", True)


def test_a_theme_switch_while_an_underlay_shows(tmp_path, mixer, monkeypatch):
    from ctgedit.ui.style import theme

    w = mixer
    _bare(w)
    ul = _underlay_image(w, _picture(tmp_path / "ref.png", 300, 200, (0, 0, 255)), monkeypatch)
    ul.opacity, ul.tint = 1.0, "none"
    ul.touch()
    w.underlay_changed()
    w.act_underlay_settings.trigger()
    _pump(2)
    app = QtWidgets.QApplication.instance()
    try:
        for scheme in ("light", "dark"):
            theme.apply_theme(app, scheme)
            _pump(3)
            img = _grab(w)
            c = _px(w, img, 50, 50)
            assert (c.red(), c.green(), c.blue()) == (0, 0, 255), (scheme, c.name())
            assert w._dialogs.get("underlay").isVisible()
            assert w.status_underlay.text() == "Underlay 100 %"
    finally:
        theme.apply_theme(app, "dark")


def test_the_underlay_window_follows_the_tab_and_every_control_has_a_tip(tmp_path, lib, monkeypatch):
    a = _copy(UNDER, tmp_path / "a")
    b = _copy(FIRST, tmp_path / "b")
    w = _window(a)
    try:
        _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
        w.act_underlay_settings.trigger()
        _pump(2)
        dlg = w._dialogs.get("underlay")
        assert dlg.windowTitle() == "Underlay - " + UNDER
        assert dlg.source.text() == "ref.png" and dlg.cb_show.isChecked()
        for c in dlg._controls + (dlg.btn_change,):
            assert c.toolTip(), c
        from ctgedit.ui.help import DIALOG_TOPICS

        assert "UnderlayDialog" in DIALOG_TOPICS and "VersionPickerDialog" in DIALOG_TOPICS
        w.open_document(b)
        _pump(3)
        assert dlg.isVisible() and dlg.windowTitle() == "Underlay - " + FIRST
        assert dlg.btn_change.isEnabled() and not dlg.cb_show.isEnabled()
        assert dlg.source.text() == "No underlay" and dlg.btn_change.text() == "Choose Underlay…"
        # (with no source only the way to choose one: its settings and Remove come with one)
        assert dlg.cb_show.isHidden() and dlg.btn_remove.isHidden() and dlg.cb_snap.isHidden()
        # Esc closes it
        dlg.activateWindow()
        QtTest.QTest.keyClick(dlg, QtCore.Qt.Key_Escape)
        _pump(2)
        assert not dlg.isVisible()
    finally:
        _close(w)


# ======================================= move with the mouse, placing near the underlay
def _send(view, kind, at, buttons=NOBUTTON):
    """One mouse event to the viewport, through its event filters."""
    QtWidgets.QApplication.sendEvent(view.viewport(), _me(view, kind, QtCore.QPoint(at), buttons))


def _messages(win, part):
    from ctgedit.ui.notify import centre

    return [m for m in centre(win).entries if part in m.text]


def test_closing_the_window_or_another_tool_ends_move_with_the_mouse_where_it_is(
    tmp_path, mixer, monkeypatch
):
    """Closing the Underlay window (Esc) ends Move with the Mouse with the underlay where it
    is, and a drag on the canvas moves objects again; another tool ends it too; turning it on
    makes Select the tool."""
    from ctgedit.ui.editor import underlay as U

    w, v = mixer, mixer.view
    ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    i = w.doc.create_object("rectangle", (300, OY + 300, 400, OY + 360), None, colour=0x3366CC)
    w._refresh()
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(400, OY + 300))
    v.set_snap(False)
    v.set_smart_guides(False)
    _pump(2)
    w.act_underlay_settings.trigger()
    _pump(2)
    dlg = w._dialogs.get("underlay")
    dlg.btn_move.click()
    assert ul.moving and w.act_underlay_move.isChecked() and dlg.btn_move.isChecked()
    _drag(v, _sp(v, 200, OY + 100), _sp(v, 240, OY + 130), through_filters=True)
    assert (ul.x, ul.y) == (40.0, 30.0)
    dlg.activateWindow()
    QtTest.QTest.keyClick(dlg, QtCore.Qt.Key_Escape)
    _pump(2)
    assert not dlg.isVisible()
    assert not ul.moving and (ul.x, ul.y) == (40.0, 30.0)
    assert not w.act_underlay_move.isChecked()
    kept = U.remembered(w.doc)
    assert (kept.x, kept.y) == (40.0, 30.0)
    # the canvas's own drag again: the object moves, the underlay stays
    w._set_selection([i])
    v.select_units([[i]], reveal=False)
    _drag(v, _sp(v, 350, OY + 330), _sp(v, 377, OY + 330), through_filters=True)
    assert w.doc.page.objects[i].geometry.x == 327
    assert (ul.x, ul.y) == (40.0, 30.0)
    # another tool ends it where it is
    w.act_underlay_move.trigger()
    assert ul.moving
    _drag(v, _sp(v, 200, OY + 100), _sp(v, 210, OY + 100), through_filters=True)
    w.tool_actions["rectangle"].trigger()
    assert not ul.moving and (ul.x, ul.y) == (50.0, 30.0) and v.tool() == "rectangle"
    # turned on with a drawing tool: Select is the tool (a drawing tool took the drag)
    w.act_underlay_move.trigger()
    assert ul.moving and v.tool() == "select"
    w.act_underlay_move.trigger()
    assert not ul.moving and (ul.x, ul.y) == (50.0, 30.0)


def test_move_with_the_mouse_is_a_command_the_status_field_shows_and_enter_ends(tmp_path, mixer, monkeypatch):
    from ctgedit.ui.main_window_base import DESCRIPTIONS
    from ctgedit.ui.dialog.palette import harvest_actions

    w, v = mixer, mixer.view
    a = w.act_underlay_move
    rows = {x.objectName(): crumb for x, crumb, _l in harvest_actions(w.menuBar())}
    assert rows.get("act_underlay_move") == "View > Underlay"
    assert a.isCheckable() and not a.icon().isNull() and DESCRIPTIONS["act_underlay_move"]
    assert not a.isEnabled()  # (no underlay)
    ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    w._update_actions()
    assert a.isEnabled() and not a.isChecked()
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(400, 300))
    _pump(2)
    a.trigger()
    f = w.status_underlay
    assert ul.moving and a.isChecked()
    assert f.text() == "Moving the underlay" and "Enter" in f.toolTip()
    _drag(v, _sp(v, 300, 300), _sp(v, 340, 330), through_filters=True)
    assert (ul.x, ul.y) == (40.0, 30.0)
    # a click on the status field ends it, where it is (no double-click wait)
    QtTest.QTest.mouseClick(f, LEFT)
    _pump(1)
    assert not ul.moving and (ul.x, ul.y) == (40.0, 30.0) and f.text() == "Underlay 40 %"
    # Enter on the canvas ends it too, keeping the place (Esc is not the only key)
    a.trigger()
    _drag(v, _sp(v, 300, 300), _sp(v, 310, 305), through_filters=True)
    v.setFocus()
    QtTest.QTest.keyClick(v, QtCore.Qt.Key_Return)
    assert not ul.moving and (ul.x, ul.y) == (50.0, 35.0)
    # hidden: it cannot be moved
    w.act_underlay_show.trigger()
    w._update_actions()
    assert not a.isEnabled()


def test_move_with_the_mouse_leaves_panning_scrolling_and_the_readout_alone(tmp_path, mixer, monkeypatch):
    """While it moves, a pointer move without a button passes on (the readout follows, the
    thin scroll bar shows and takes the mouse), Space + drag pans, there is no hover outline,
    and a scroll in the middle of a drag keeps the grabbed point under the pointer."""
    w, v = mixer, mixer.view
    ul = _underlay_image(w, _picture(tmp_path / "ref.png", 300, 200), monkeypatch)
    v.overlay_bars.set_mode("auto")
    v.set_zoom(4.0)
    v.centerOn(QtCore.QPointF(400, 300))
    _pump(3)
    w.act_underlay_move.trigger()
    assert ul.moving
    vp = v.viewport()
    seen = []
    v.hoverPos.connect(lambda x, y: seen.append((x, y)))
    # the pointer readout follows; the canvas shows the open hand and no hover outline
    root = next(
        i
        for i in w.doc.page.roots()
        if (w.doc.page.objects[i].geometry or None) is not None
        and w.doc.page.objects[i].geometry.extent_x > 20
    )
    g = w.doc.page.objects[root].geometry
    v.centerOn(QtCore.QPointF(g.x + g.extent_x / 2, g.y + g.extent_y / 2))
    _pump(2)
    _send(v, QtCore.QEvent.MouseMove, _sp(v, g.x + g.extent_x / 2, g.y + g.extent_y / 2))
    assert seen and v._hover_unit == []
    assert vp.cursor().shape() == QtCore.Qt.OpenHandCursor
    # the thin scroll bar at the right edge shows and takes the mouse
    edge = QtCore.QPoint(vp.width() - 3, vp.height() // 2)
    _send(v, QtCore.QEvent.MouseMove, QtCore.QPoint(vp.width() - 40, 200))
    _send(v, QtCore.QEvent.MouseMove, edge)
    _pump(1)
    assert v.overlay_bars.v.isVisible() and v.overlay_bars.v.hot()
    x0 = (ul.x, ul.y)
    assert v.on_scroll_bar(QtCore.QPointF(edge))
    _send(v, QtCore.QEvent.MouseButtonPress, edge, LEFT)
    _send(v, QtCore.QEvent.MouseButtonRelease, edge)
    assert (ul.x, ul.y) == x0
    _send(v, QtCore.QEvent.MouseMove, QtCore.QPoint(200, 200))
    v.overlay_bars.v.set_hot(False)
    # Space + drag pans the page, the underlay stays
    hb = v.horizontalScrollBar()
    h0 = hb.value()
    v.begin_space_pan()
    try:
        _drag(v, QtCore.QPoint(300, 300), QtCore.QPoint(200, 300), through_filters=True)
    finally:
        v.end_space_pan()
    assert hb.value() != h0 and (ul.x, ul.y) == x0
    # a scroll while the button is held: the grabbed point stays under the pointer
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(600, 400))
    _pump(2)
    a = _sp(v, 300, 300)
    _send(v, QtCore.QEvent.MouseButtonPress, a, LEFT)
    b = a + QtCore.QPoint(40, 0)
    _send(v, QtCore.QEvent.MouseMove, b, LEFT)
    assert (ul.x - x0[0]) == 40.0
    hb.setValue(hb.value() + 100)
    _pump(1)
    _send(v, QtCore.QEvent.MouseMove, b, LEFT)
    _send(v, QtCore.QEvent.MouseButtonRelease, b)
    assert ul.x - x0[0] == 140.0, ul.x


def test_a_symbol_or_picture_placed_near_an_underlay_object_lands_on_its_corner(
    tmp_path, mixer, fake_render, monkeypatch
):
    w, v = mixer, mixer.view
    fake_render.boxes = [[500.0, 100.0, 80.0, 40.0]]
    ul = _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
    w.move_underlay_to(2500, OY)  # the box at (3000, OY + 100)
    v.set_zoom(1.0)
    if not w.act_snap.isChecked():
        w.act_snap.trigger()
    placed = []
    monkeypatch.setattr(
        w.doc, "place_library_element", lambda _l, _e, _x, at, *a, **k: placed.append(tuple(at))
    )
    # a Library card dropped a few pixels off the reference's corner: on the corner, not on
    # the nearest grid point
    w._on_library_drop("Lib", "pump", "ctl", QtCore.QPointF(3003, OY + 103))
    assert placed[-1] == (3000, OY + 100), placed
    assert v.grid_point(3003, OY + 103) != (3000, OY + 100)
    # far from it: the grid as before
    w._on_library_drop("Lib", "pump", "ctl", QtCore.QPointF(3203, OY + 303))
    assert placed[-1] == v.grid_point(3203, OY + 303)
    # a picture placed at a point: the same
    assert w._place_point((2998, OY + 99), 10, 10) == (3000, OY + 100)
    # its snapping off: the grid only
    ul.snap = False
    assert w._place_point((3003, OY + 103), 10, 10) == v.grid_point(3003, OY + 103)


def test_a_new_underlay_says_where_it_lies_with_a_way_to_its_window(
    tmp_path, mixer, fake_render, monkeypatch
):
    w = mixer
    _underlay_image(w, _picture(tmp_path / "plant.png"), monkeypatch)
    msgs = _messages(w, "Underlaid plant.png")
    assert len(msgs) == 1 and "100 %" in msgs[0].text and msgs[0].level == "success"
    adjust = [x for x in msgs[0].actions if x.label.startswith("Adjust")]
    assert adjust and adjust[0].can_run()
    adjust[0].fn()
    _pump(2)
    dlg = w._dialogs.get("underlay")
    assert dlg is not None and dlg.isVisible()
    # a page: said once it is drawn (the render process: here its stand-in)
    _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
    page_msgs = _messages(w, "Underlaid " + SOURCE)
    assert len(page_msgs) == 1 and "lined up" in page_msgs[0].text
    # a reload says nothing more
    w.reload_underlay()
    from ctgedit.ui.editor import underlay as U

    assert U.render_queue().wait(30)
    _pump(2)
    assert len(_messages(w, "Underlaid " + SOURCE)) == 1
    # the page itself, picked on purpose: drawn as it was saved, and said so
    _underlay_page(w, w.doc.path, monkeypatch)
    assert _messages(w, "as it was last saved")


def test_the_window_without_an_underlay_shows_what_a_new_one_starts_with(tmp_path, lib, monkeypatch):
    a = _copy(UNDER, tmp_path / "a")
    b = _copy(FIRST, tmp_path / "b")
    w = _window(a)
    try:
        ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
        w.act_underlay_settings.trigger()
        _pump(2)
        dlg = w._dialogs.get("underlay")
        dlg.cb_tint.setCurrentIndex(dlg.cb_tint.findData("blue"))
        dlg.cb_above.setChecked(True)
        dlg.btn_fit.click()
        dlg.sp_opacity.setValue(70)
        assert ul.tint == "blue" and ul.above and ul.scale != 1.0
        w.open_document(b)
        _pump(3)
        assert w.underlay() is None and not dlg.cb_show.isEnabled()
        assert dlg.cb_show.isChecked() and not dlg.cb_above.isChecked()
        assert dlg.cb_tint.currentData() == "grey" and dlg.sp_opacity.value() == 40
        assert (dlg.sp_x.value(), dlg.sp_y.value(), dlg.sp_scale.value()) == (0.0, 0.0, 100.0)
        assert not dlg.btn_retry.isVisible()
    finally:
        _close(w)


def test_the_page_picker_does_not_select_the_page_itself_first(tmp_path, lib, monkeypatch):
    """Underlay a Page…: the page shown is not the first selection (one click underlaid it
    under itself); it can still be picked, and is then drawn as it was saved."""
    _app()
    from ctgedit.ui.browser.dialog import MODE_OPEN, PageBrowserDialog

    folder = tmp_path / "pages"
    first = _copy(FIRST, folder)  # ('!FxSystem' sorts first)
    _copy(UNDER, folder)
    dlg = PageBrowserDialog(
        MODE_OPEN,
        None,
        start_path=first,
        kinds=("page",),
        title="Underlay a Page",
        ok_text="Underlay",
        avoid_path=first,
    )
    try:
        _pump(3)
        e = dlg.current_entry()
        assert e is not None and os.path.normcase(e.path) != os.path.normcase(first), e
    finally:
        dlg.deleteLater()
    # the same page named another way (relative, in other letters): avoided too
    monkeypatch.chdir(os.path.dirname(first))
    dlg = PageBrowserDialog(
        MODE_OPEN, None, start_path=os.path.relpath(first), kinds=("page",), avoid_path=first.upper()
    )
    try:
        assert dlg._want_select is None
        _pump(3)
        e = dlg.current_entry()
        assert e is not None and os.path.normcase(e.path) != os.path.normcase(first), e
    finally:
        dlg.deleteLater()
    plain = PageBrowserDialog(MODE_OPEN, None, start_path=first, kinds=("page",))
    try:
        _pump(3)
        e = plain.current_entry()
        assert e is not None and os.path.normcase(e.path) == os.path.normcase(first)
    finally:
        plain.deleteLater()
    # the command asks the browser to avoid the page shown
    from ctgedit.ui.editor import underlay as U
    from ctgedit.ui.browser import PageBrowserDialog as PBD

    asked = {}
    monkeypatch.setattr(PBD, "get_open_path", staticmethod(lambda *a, **k: asked.update(k)))
    doc = types.SimpleNamespace(path=first)
    U.ask_page(None, doc)
    assert asked["avoid_path"] == first and asked["start_path"] == first


def test_a_failed_underlay_opens_its_window_from_the_status_field_and_tries_again(
    tmp_path, mixer, monkeypatch
):
    from ctgedit.ui.editor import underlay as U

    w = mixer
    pic = str(tmp_path / "away.png")
    ul = _underlay_image(w, pic, monkeypatch)  # (not there yet: a share away)
    assert ul.status == "error" and "not there" in ul.error
    f = w.status_underlay
    assert f.text() == "Underlay failed" and "Underlay window" in f.toolTip()
    msg = _messages(w, "could not be drawn")[-1]
    assert [x.label for x in msg.actions] == ["Try Again"]
    QtTest.QTest.mouseClick(f, LEFT)
    _pump(2)
    dlg = w._dialogs.get("underlay")
    assert dlg is not None and dlg.isVisible() and ul.shown  # (no hide)
    assert dlg.btn_retry.isVisible() and "could not be drawn" in dlg.note.text()
    dlg.btn_retry.click()
    assert ul.status == "error"
    _picture(pic)
    dlg.btn_retry.click()
    _pump(1)
    assert ul.status == "ready" and not dlg.btn_retry.isVisible()
    assert f.text() == "Underlay 40 %"
    # a page that failed is drawn again when its file comes back (the next update)
    w.remove_underlay()
    os.rename(pic, pic + ".away")
    ul = _underlay_image(w, pic, monkeypatch)
    assert ul.status == "error"
    os.rename(pic + ".away", pic)
    w._update_actions()
    assert ul.status == "ready"
    assert U.peek(w.doc) is ul


def test_one_full_picture_per_underlay_and_the_tab_left_lets_go_of_its_copy(
    tmp_path, lib, fake_render, monkeypatch
):
    """Grey: the grey values and the tinted copy are kept, the picture as it came goes; the
    tab left lets go of the tinted copy; Tint None draws the picture as it came again (one
    copy: what is drawn is that picture)."""
    from ctgedit.ui.editor import underlay as U

    a = _copy(UNDER, tmp_path / "a")
    b = _copy(FIRST, tmp_path / "b")
    w = _window(a)
    try:
        ul = _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
        assert ul.status == "ready" and ul.tint == "grey"
        assert ul.image is None and ul._grey is not None
        _grab(w)
        assert ul._drawn is not None and ul._drawn[1].format() in (
            QtGui.QImage.Format_ARGB32_Premultiplied,
            QtGui.QImage.Format_RGB32,
        )
        assert ul._tiles  # (its drawn tiles)
        doc_a = w.doc
        w.open_document(b)
        _pump(2)
        assert ul._drawn is None and ul._levels == {} and ul.ready()
        assert not ul._tiles
        w.set_document(doc_a)
        _pump(2)
        _grab(w)
        assert ul._drawn is not None
        # Tint None: drawn again from its file (from the cache here), and drawn as it is
        calls = len(fake_render.calls)
        w.act_underlay_settings.trigger()
        _pump(2)
        dlg = w._dialogs.get("underlay")
        dlg.cb_tint.setCurrentIndex(dlg.cb_tint.findData("none"))
        assert U.render_queue().wait(30)
        _pump(2)
        assert ul.tint == "none" and ul.image is not None and not ul.need_raw
        assert len(fake_render.calls) == calls  # (from the cache)
        _grab(w)
        assert ul.drawn().cacheKey() == ul.image.cacheKey()  # (no second copy)
        # Grey again: made from the picture, which then goes
        dlg.cb_tint.setCurrentIndex(dlg.cb_tint.findData("grey"))
        assert ul.image is None and ul._grey is not None
    finally:
        _close(w)


def test_a_picture_there_is_no_memory_for_fails_once(tmp_path, mixer, monkeypatch):
    """A tinted copy that cannot be made (Qt gives a null picture without the memory): the
    underlay fails once with the reason, no paint tries again."""
    from ctgedit.ui.editor import underlay as U

    w = mixer
    ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    calls = []

    def no_memory(base, tint):
        calls.append(tint)
        return QtGui.QImage()

    monkeypatch.setattr(U, "tint_from_base", no_memory)
    ul.release_drawn()
    _grab(w)
    _grab(w)
    assert len(calls) == 1
    assert ul.status == "error" and "not enough memory" in ul.error and not ul.ready()
    _pump(2)
    assert w.status_underlay.text() == "Underlay failed"
    assert _messages(w, "not enough memory")


def test_the_tint_is_made_with_qt_conversions_quickly(tmp_path):
    """Grey / Blue from the grey values and the alpha: the colours and the alpha, and quickly."""
    import time

    _app()
    from ctgedit.ui.editor import underlay as U

    img = QtGui.QImage(4, 1, QtGui.QImage.Format_ARGB32)
    img.setPixelColor(0, 0, QtGui.QColor(200, 200, 200, 255))
    img.setPixelColor(1, 0, QtGui.QColor(0, 0, 0, 0))
    img.setPixelColor(2, 0, QtGui.QColor(255, 255, 255, 128))
    img.setPixelColor(3, 0, QtGui.QColor(20, 200, 30, 255))
    g = U.tint_image(img, "grey")
    assert g.format() == QtGui.QImage.Format_ARGB32_Premultiplied
    c0, c1, c2, c3 = (g.pixelColor(k, 0) for k in range(4))
    assert (c0.red(), c0.green(), c0.blue(), c0.alpha()) == (200, 200, 200, 255)
    assert c1.alpha() == 0 and abs(c2.alpha() - 128) <= 1 and c2.red() >= 250
    assert c3.red() == c3.green() == c3.blue()
    blue = U.tint_image(img, "blue").pixelColor(0, 0)
    assert blue.blue() > blue.red()
    # an opaque picture stays opaque (RGB32: no alpha to keep)
    opaque = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
    opaque.fill(QtGui.QColor(10, 20, 30))
    assert U.tint_image(opaque, "grey").format() == QtGui.QImage.Format_RGB32
    big = QtGui.QImage(4000, 2500, QtGui.QImage.Format_ARGB32_Premultiplied)
    big.fill(QtGui.QColor(30, 120, 220, 255))
    t0 = time.perf_counter()
    out = U.tint_image(big, "grey")
    took = time.perf_counter() - t0
    assert not out.isNull()
    check_speed(took, 1.0, "the tint")


def test_a_large_picture_is_read_with_the_allocation_limit_raised_for_that_read(tmp_path):
    """PNG decodes the whole picture before it scales it, so Qt's allocation limit is raised
    for that one read (and put back); over READ_LIMIT_MB the picture is refused with its size.
    (Qt's limit is lowered here, so a small picture stands for a 70 M pixel scan.)"""
    _app()
    from ctgedit.ui.editor import underlay as U

    pic = _picture(tmp_path / "scan.png", 2000, 1500, (40, 90, 160))  # 12 MB decoded
    limit = QtGui.QImageReader.allocationLimit()
    QtGui.QImageReader.setAllocationLimit(8)
    try:
        img, unit, err = U.read_picture(pic)
        assert img is not None and not err and (img.width(), img.height()) == (2000, 1500)
        assert QtGui.QImageReader.allocationLimit() == 8  # (put back)
        # read scaled down (a picture over MAX_PIXELS): still decoded whole first
        mp = U.MAX_PIXELS
        U.MAX_PIXELS = 1_000_000
        try:
            img, unit, err = U.read_picture(pic)
        finally:
            U.MAX_PIXELS = mp
        assert img is not None and img.width() * img.height() <= 1_000_000 and unit < 1.0
        assert QtGui.QImageReader.allocationLimit() == 8
        # too large to read at all: said with its size
        lim = U.READ_LIMIT_MB
        U.READ_LIMIT_MB = 10
        try:
            img, unit, err = U.read_picture(pic)
        finally:
            U.READ_LIMIT_MB = lim
        assert img is None and "too large" in err and "2000 × 1500" in err
    finally:
        QtGui.QImageReader.setAllocationLimit(limit)


def test_the_picture_cache_is_small_follows_libraries_and_the_label_mode(tmp_path):
    _app()
    from ctgedit.infra import caches
    from ctgedit.io import reader
    from ctgedit.ui.editor import underlay as U

    U._IMAGE_CACHE.clear()
    small = QtGui.QImage(100, 100, QtGui.QImage.Format_ARGB32_Premultiplied)
    U._cache_put(("a",), small, {})
    assert ("a",) in U._IMAGE_CACHE
    # a picture larger than the cache is not kept beside the one drawn
    big = QtGui.QImage(5000, 4000, QtGui.QImage.Format_RGB32)
    U._cache_put(("b",), big, {})
    assert ("b",) not in U._IMAGE_CACHE
    # a library saved empties it (a page underlay draws genies and symbols)
    assert "ui.underlay.images" in caches.clear("libraries") and not U._IMAGE_CACHE
    # the label mode is part of a page's key
    ul = U.Underlay("page", str(tmp_path / "p.ctg"))
    mode = reader.get_label_mode()
    try:
        reader.set_label_mode("literal")
        k1 = U.page_cache_key(ul, (1, 2), 2)
        reader.set_label_mode("native")
        k2 = U.page_cache_key(ul, (1, 2), 2)
    finally:
        reader.set_label_mode(mode)
    assert k1 != k2


def test_point_snapping_finds_the_nearest_edge_without_rebuilding_the_targets(
    tmp_path, mixer, fake_render, monkeypatch
):
    """5000 underlay boxes: a snapped point looks in the sorted edges (the provider's
    SnapTargets is made once per place), well under a millisecond a point."""
    import random
    import time
    from ctgedit.ui.editor import canvas_hooks as CH

    w, v = mixer, mixer.view
    rnd = random.Random(7)
    fake_render.boxes = [
        [
            float(rnd.randrange(0, 1300)),
            float(rnd.randrange(0, 700)),
            float(rnd.randrange(4, 60)),
            float(rnd.randrange(4, 40)),
        ]
        for _ in range(5000)
    ]
    ul = _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
    assert len(ul.boxes) == 5000
    v.set_snap(False)
    made = []
    real = CH.SnapTargets.__init__

    def counting(self, rects=()):
        made.append(1)
        real(self, rects)

    monkeypatch.setattr(CH.SnapTargets, "__init__", counting)
    pts = [QtCore.QPointF(rnd.uniform(0, PAGE_W), rnd.uniform(0, PAGE_H)) for _ in range(300)]
    t0 = time.perf_counter()
    got = [v._snap_pt(p) for p in pts]
    took = (time.perf_counter() - t0) / len(pts)
    assert len(made) <= 1, len(made)
    check_speed(took, 0.001, "a snap")
    # the same answers as a plain scan of every edge and centre
    tol = v._guide_tol(0)
    rects = ul.snap_rects()
    for p, q in zip(pts[:60], got[:60]):
        for axis, val, res in ((0, p.x(), q.x()), (1, p.y(), q.y())):
            vals = [
                x
                for r in rects
                for x in (
                    (r.left(), r.center().x(), r.right())
                    if axis == 0
                    else (r.top(), r.center().y(), r.bottom())
                )
            ]
            near = [x for x in vals if abs(x - val) <= tol]
            if near:
                best = min(near, key=lambda x: abs(x - val))
                assert abs(res - best) <= 0.5 + 1e-9, (axis, val, res, best)
            else:
                assert res == round(val)


def test_underlay_snap_targets_are_whole_page_pixels(tmp_path, mixer, fake_render, monkeypatch):
    """A fractional X (or a scale other than 100 %): an object snapped to an edge is stored
    on it, not a pixel short."""
    w, v = mixer, mixer.view
    fake_render.boxes = [[501.0, 100.0, 61.0, 40.0]]
    ul = _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
    ul.x = 10.6
    ul.touch()
    r = ul.snap_rects()[0]
    assert (r.left(), r.right()) == (512.0, 573.0)
    v.set_snap(False)
    assert v._snap_pt(QtCore.QPointF(513.4, 50.0)).x() == 512.0
    # a centre half way (542.5) is caught on a whole pixel
    assert v._snap_pt(QtCore.QPointF(541.0, 50.0)).x() in (542.0, 543.0)
    # Fit-like scale: 75 %
    ul.x, ul.scale = 0.0, 0.75
    ul.touch()
    r = ul.snap_rects()[0]
    assert r.left() == float(int(r.left())) and r.right() == float(int(r.right()))
    # a rectangle drawn from beside the edge starts on it
    ul.x, ul.scale = 10.6, 1.0
    ul.touch()
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(540, 300))
    _pump(2)
    w.tool_actions["rectangle"].trigger()
    n = len(w.doc.page.objects)
    _drag(v, _sp(v, 513, 300), _sp(v, 600, 360))
    assert len(w.doc.page.objects) > n
    new = [o for o in w.doc.page.objects[n:] if o.geometry is not None]
    assert new and min(o.geometry.x for o in new) == 512


def test_an_object_never_snaps_to_its_own_place_on_an_underlaid_version(
    tmp_path, mixer, fake_render, monkeypatch
):
    """An earlier version (or the page itself) underlaid has each unchanged object's box at
    its place: the boxes of what is dragged or resized are left out, the others still catch."""
    w, v = mixer, mixer.view
    i = w.doc.create_object("rectangle", (2800, OY + 200, 2860, OY + 240), None, colour=0x3366CC)
    w._refresh()
    # the reference has the same object at the same place, and another one 100 px right
    fake_render.boxes = [[2800.0, OY + 200.0, 60.0, 40.0], [2960.0, OY + 200.0, 60.0, 40.0]]
    fake_render.page = (4000, OY + 400)
    _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(2900, OY + 220))
    v.set_snap(False)
    _pump(2)

    def drag_by(dx):
        w._set_selection([i])
        v.select_units([[i]], reveal=False)
        g0 = w.doc.page.objects[i].geometry.x
        _drag(v, _sp(v, g0 + 30, OY + 220), _sp(v, g0 + 30 + dx, OY + 220))
        return w.doc.page.objects[i].geometry.x - g0

    assert drag_by(5) == 5
    w.act_undo.trigger()
    _pump(2)
    assert drag_by(9) == 9
    w.act_undo.trigger()
    _pump(2)
    # the grid on: one grid step moves it one grid step
    v.set_snap(True)
    pitch = v._pitch()[0]
    assert drag_by(pitch) == pitch
    w.act_undo.trigger()
    _pump(2)
    v.set_snap(False)
    # the reference's other object still catches (its left edge at 2960: 97 -> 100)
    assert drag_by(97) == 100
    w.act_undo.trigger()
    _pump(2)
    # a resize by 3 px moves the edge 3 px (it snapped back to the start box)
    w._set_selection([i])
    v.select_units([[i]], reveal=False)
    _pump(1)
    g = w.doc.page.objects[i].geometry
    r0 = g.x + g.extent_x
    _drag(v, _sp(v, r0, OY + 220), _sp(v, r0 + 3, OY + 220))
    g = w.doc.page.objects[i].geometry
    assert g.x + g.extent_x == r0 + 3


def test_closing_a_tab_stops_its_underlay_render_and_a_cancel_stops_an_orphan(tmp_path, lib, monkeypatch):
    import gc
    from ctgedit.ui.jobs import tasks
    from ctgedit.ui.editor import underlay as U

    held = []
    monkeypatch.setattr(U, "request_render", lambda queue, token, path, w_, h, options: held.append(token))
    a = _copy(UNDER, tmp_path / "a")
    b = _copy(FIRST, tmp_path / "b")
    w = _window(a)
    try:
        monkeypatch.setattr(U, "ask_page", lambda _w, _d: _copy(SOURCE, tmp_path / "s1"))
        w.act_underlay_page.trigger()
        _pump(2)
        ul_a = U.peek(w.doc)
        w.open_document(b)
        _pump(2)
        monkeypatch.setattr(U, "ask_page", lambda _w, _d: _copy(SOURCE, tmp_path / "s2"))
        w.act_underlay_page.trigger()
        _pump(2)
        ul_b = w.underlay()
        q = U.render_queue()
        assert q._current["ul"]() is ul_a and len(q._pending) == 1 and len(held) == 1
        w._docs[0]["doc"].dirty = False
        w._close_doc_tab(0, ask=False)
        _pump(2)
        # tab A's render stopped: tab B's runs now, its task is the only one
        assert q._current is not None and q._current["ul"]() is ul_b and len(held) == 2
        assert [t.text for t in tasks.running()] == [U.DRAWING_NOTE % ul_b.name()]
        assert ul_a.status == "loading" and not ul_a.ready()
        # an orphan: its underlay gone (no page has it) while it is drawn; its Cancel still
        # stops it and the next one starts
        U._set(w.doc, None)
        del ul_b
        gc.collect()
        _pump(1)
        run = tasks.running()
        assert len(run) == 1
        tasks.cancel(run[0].token)
        _pump(1)
        assert q._current is None and not tasks.running()
    finally:
        _close(w)


def test_revert_to_saved_keeps_the_underlay(tmp_path, mixer, monkeypatch):
    w = mixer
    ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    w.move_underlay_to(12, 34)
    w.doc.create_object("rectangle", (10, OY + 10, 50, OY + 50), None, colour=0x3366CC)
    w._refresh()
    assert w.revert_document(ask=False)
    _pump(2)
    assert w.underlay() is ul and (ul.x, ul.y) == (12.0, 34.0)


def test_the_render_process_leaves_out_objects_off_its_page(tmp_path, mixer):
    """root_boxes: an object wholly off the source page is not drawn there, so it is no
    target; one partly on it is."""
    from ctgedit.ui.browser import preview

    w = mixer
    pw, ph = w.doc.page.appearance.width, w.doc.page.appearance.height
    off = w.doc.create_object("rectangle", (pw + 100, ph + 100, pw + 200, ph + 160), None, colour=0x3366CC)
    part = w.doc.create_object("rectangle", (pw - 20, 10, pw + 40, 50), None, colour=0x3366CC)
    boxes = [tuple(b) for b in preview.root_boxes(w.doc.page)]
    go = w.doc.page.objects[off].geometry
    gp = w.doc.page.objects[part].geometry
    assert (float(go.x), float(go.y), float(go.extent_x), float(go.extent_y)) not in boxes
    assert (float(gp.x), float(gp.y), float(gp.extent_x), float(gp.extent_y)) in boxes


def test_a_reload_keeps_the_tinted_copy_until_the_new_picture_comes(
    tmp_path, mixer, fake_render, monkeypatch
):
    """Draw its template (a reload) while the old picture shows: no tint made again for the
    old picture, one for the new one."""
    from ctgedit.ui.editor import underlay as U

    w = mixer
    ul = _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
    _grab(w)
    assert ul._drawn is not None
    calls = []
    real = U.tint_from_base
    monkeypatch.setattr(U, "tint_from_base", lambda base, tint: calls.append(tint) or real(base, tint))
    deliver = []
    monkeypatch.setattr(
        U, "request_render", lambda queue, token, path, w_, h, options: deliver.append((queue, token))
    )
    ul.template = True
    w.reload_underlay()
    assert ul.status == "loading" and ul.ready()
    _grab(w)
    _grab(w)
    assert calls == []  # (the old copy still shows)
    queue, token = deliver[-1]
    img = QtGui.QImage(200, 100, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtGui.QColor(0, 0, 0, 255))
    queue._on_rendered(token, img, {"page": [100, 50], "boxes": []}, "")
    _pump(1)
    _grab(w)
    _grab(w)
    assert calls == ["grey"]


# ========================================= corners, scrolled drawing, zoom while moving
def test_a_placed_element_takes_the_nearest_corner_not_each_axis_nearest_edge(
    tmp_path, mixer, fake_render, monkeypatch
):
    """Placement looks for a corner first: on a busy reference the nearest left (or top) edge
    is often another object's, far away."""
    w, v = mixer, mixer.view
    # A: the corner to place on; B: its left edge 2 px right of A's, 300 px lower; C: its
    # top 1 px below A's, 400 px to the right; D: a corner a little further off than A's
    fake_render.boxes = [
        [500.0, 100.0, 80.0, 40.0],
        [502.0, 400.0, 60.0, 30.0],
        [900.0, 101.0, 50.0, 50.0],
        [506.0, 97.0, 30.0, 20.0],
    ]
    _underlay_page(w, _copy(SOURCE, tmp_path / "src"), monkeypatch)
    w.move_underlay_to(2500, OY)  # A's corner at (3000, OY + 100)
    v.set_zoom(1.0)
    if not w.act_snap.isChecked():
        w.act_snap.trigger()
    placed = []
    monkeypatch.setattr(
        w.doc, "place_library_element", lambda _l, _e, _x, at, *a, **k: placed.append(tuple(at))
    )
    # each axis on its own would take B's left (1 px off) and C's top (2 px off): (3002, OY + 101)
    w._on_library_drop("Lib", "pump", "ctl", QtCore.QPointF(3003, OY + 103))
    assert placed[-1] == (3000, OY + 100), placed
    assert w._place_point((3003, OY + 103), 10, 10) == (3000, OY + 100)
    # of two corners in reach the nearer one (the larger of its two distances)
    assert v.placement_point(3005, OY + 99) == (3006, OY + 97)
    # no corner in reach: each axis on its own (B's left edge, the grid's row)
    gy = v.grid_point(3003, OY + 300)[1]
    assert v.placement_point(3003, OY + 300) == (3002, gy)


def test_a_scrolled_underlay_draws_the_same_pixels_as_a_full_repaint(tmp_path, mixer, monkeypatch):
    """A scroll repaints only the strip it exposes (the cached background, under the
    objects); the strip is the same pixels as a full repaint, so no seam shows. Smooth or
    not, under or above the objects."""
    import random
    from ctgedit.ui.editor import underlay as U

    w, v = mixer, mixer.view
    _bare(w)
    rnd = random.Random(1)
    pw, ph = 300, 200
    data = bytes(rnd.getrandbits(8) if k % 4 != 3 else 255 for k in range(pw * ph * 4))
    noise = QtGui.QImage(data, pw, ph, pw * 4, QtGui.QImage.Format_ARGB32).copy()
    path = str(tmp_path / "noise.png")
    assert noise.save(path, "PNG")
    ul = _underlay_image(w, path, monkeypatch)
    hb, vb = v.horizontalScrollBar(), v.verticalScrollBar()
    for above in (False, True):
        for smooth in (True, False):
            ul.opacity, ul.tint, ul.scale, ul.above, ul.smooth = 1.0, "none", 1.37, above, smooth
            ul.touch()
            w.underlay_changed()
            v.set_zoom(3.3)
            v.centerOn(QtCore.QPointF(150, 120))
            _pump(3)
            v.resetCachedContent()
            v.viewport().repaint()
            for _k in range(6):
                hb.setValue(hb.value() + 7)
                vb.setValue(vb.value() + 5)
                v.viewport().repaint()
            _pump(2)
            scrolled = canvas_picture(v)
            v.resetCachedContent()
            v.viewport().repaint()
            full = canvas_picture(v)
            assert scrolled == full, (above, smooth)
            # (it is drawn: at the middle of the canvas each colour lies within those of the
            # reference's pixels around that point, the ones it is scaled from)
            c = v.viewport().rect().center()
            mid = v.mapToScene(c)
            assert ul.rect().contains(mid)
            k = ul.pic_size[0] / ul.rect().width()
            px, py = int((mid.x() - ul.x) * k), int((mid.y() - ul.y) * k)
            near = [noise.pixelColor(px + dx, py + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            got = full.pixelColor(c)
            for part in ("red", "green", "blue"):
                vals = [getattr(n, part)() for n in near]
                assert min(vals) - 2 <= getattr(got, part)() <= max(vals) + 2, (part, smooth)
            w.act_underlay_show.trigger()  # (hidden: not those pixels)
            v.resetCachedContent()
            v.viewport().repaint()
            assert canvas_picture(v) != full
            w.act_underlay_show.trigger()
            assert ul.shown
            # the tiles kept: those shown and a few more
            vp = v.viewport().size()
            shown = (vp.width() // U.Underlay.TILE + 2) * (vp.height() // U.Underlay.TILE + 2)
            assert 0 < len(ul._tiles) <= U.Underlay.TILE_KEEP + shown


def test_the_tiles_lie_on_device_pixels_and_a_turned_painter_still_draws():
    """On a 150 % screen the tiles are laid on the device's pixels (no scaling of a scaled
    tile); a painter offset by whole pixels (a scroll) reuses them; a turned painter (never
    the canvas's) draws the picture directly."""
    _app()
    from ctgedit.ui.editor import underlay as U

    ul = U.Underlay("image", os.path.join(os.getcwd(), "not-there.png"))
    ul.tint, ul.opacity = "none", 1.0
    red = QtGui.QColor(220, 30, 30)
    pic = QtGui.QImage(40, 30, QtGui.QImage.Format_ARGB32)
    pic.fill(red)
    ul.set_picture(pic, (40, 30), [])
    ul.x, ul.y = 10.0, 6.0  # 15, 9 to 75, 54 device px

    def paint(dpr=1.0, shift=(0, 0), turn=0.0):
        out = QtGui.QImage(int(200 * dpr), int(150 * dpr), QtGui.QImage.Format_ARGB32_Premultiplied)
        out.setDevicePixelRatio(dpr)
        out.fill(QtGui.QColor(255, 255, 255))
        p = QtGui.QPainter(out)
        p.translate(*shift)
        p.rotate(turn)
        ul.draw(p, QtCore.QRectF(-50, -50, 300, 250))
        p.end()
        return out

    out = paint(1.5)
    assert out.pixelColor(15, 9) == red and out.pixelColor(74, 53) == red
    assert out.pixelColor(14, 9) != red and out.pixelColor(75, 53) != red
    assert out.pixelColor(15, 8) != red and out.pixelColor(74, 54) != red
    tiles = dict(ul._tiles)
    assert tiles and all(t is None or t.devicePixelRatio() == 1.0 for t in tiles.values())
    # moved by whole device pixels: the same tiles
    paint(1.0)
    tiles = dict(ul._tiles)
    out = paint(1.0, shift=(7, 3))
    assert out.pixelColor(17, 9) == red and out.pixelColor(16, 9) != red
    assert all(ul._tiles.get(k) is t for k, t in tiles.items())
    # turned: drawn without tiles
    out = paint(1.0, shift=(100, 0), turn=30.0)
    assert any(out.pixelColor(x, y) == red for x in range(0, 200, 3) for y in range(0, 150, 3))


def test_holding_z_while_moving_the_underlay_zooms_and_the_move_stays_on(tmp_path, mixer, monkeypatch):
    """Move with the Mouse on, Z held: a drag draws the zoom box (not a move), Esc cancels
    the box (the underlay stays), and when Z is let go the move goes on."""
    from ctgedit.ui.editor import canvas_hooks

    w, v = mixer, mixer.view
    ul = _underlay_image(w, _picture(tmp_path / "ref.png", 300, 200), monkeypatch)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(400, 300))
    _pump(2)
    w.act_underlay_move.trigger()
    assert ul.moving and v.tool() == "select"
    _drag(v, QtCore.QPoint(300, 300), QtCore.QPoint(340, 330), through_filters=True)
    assert (ul.x, ul.y) == (40.0, 30.0)

    def key(kind, k, text=""):
        QtWidgets.QApplication.sendEvent(v, QtGui.QKeyEvent(kind, k, NOMOD, text))

    key(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, "z")
    assert v.tool() == "zoom" and ul.moving
    assert canvas_hooks.pointer_mode(v) is None  # (the zoom box's pointer)
    # Esc in the middle of a zoom box: the box goes, the underlay stays where it is
    _send(v, QtCore.QEvent.MouseButtonPress, QtCore.QPoint(300, 100), LEFT)
    _send(v, QtCore.QEvent.MouseMove, QtCore.QPoint(350, 150), LEFT)
    assert v._zbox is not None
    key(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape)
    assert v._zbox is None and ul.moving and (ul.x, ul.y) == (40.0, 30.0)
    _send(v, QtCore.QEvent.MouseButtonRelease, QtCore.QPoint(350, 150))
    # a drag zooms into its box, the underlay stays
    _drag(v, QtCore.QPoint(300, 100), QtCore.QPoint(400, 200), through_filters=True)
    assert (ul.x, ul.y) == (40.0, 30.0) and v.zoom() > 2.0, v.zoom()
    # Z let go (held a second): Select again, and the move goes on
    v._zhold["t"] -= 1.0
    key(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, "z")
    _pump(1)
    assert v.tool() == "select" and ul.moving and w.act_underlay_move.isChecked()
    assert canvas_hooks.pointer_mode(v) == QtCore.Qt.OpenHandCursor
    _drag(v, QtCore.QPoint(300, 300), QtCore.QPoint(380, 300), through_filters=True)
    assert ul.x > 40.0 and ul.y == 30.0
    # and Esc on the page is the move's again: back to where it began
    key(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape)
    assert not ul.moving and (ul.x, ul.y) == (0.0, 0.0)


def test_the_underlay_window_says_the_move_keys_work_on_the_page(tmp_path, mixer, monkeypatch):
    """The note says what the keys do and where: on the page. In the window Esc closes it,
    which ends the move with the underlay where it is (as the note says)."""
    w, v = mixer, mixer.view
    ul = _underlay_image(w, _picture(tmp_path / "ref.png"), monkeypatch)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(400, 300))
    w.act_underlay_settings.trigger()
    _pump(2)
    dlg = w._dialogs.get("underlay")
    dlg.btn_move.click()
    note = dlg.move_note.text()
    assert note.startswith("Drag on the page to move it. On the page, the arrow keys")
    assert "Esc puts it back" in note and "Closing this window also ends it where it is" in note
    _drag(v, _sp(v, 300, 300), _sp(v, 340, 330), through_filters=True)
    assert (ul.x, ul.y) == (40.0, 30.0)
    dlg.activateWindow()
    dlg.sp_opacity.setFocus()
    QtTest.QTest.keyClick(dlg.sp_opacity, QtCore.Qt.Key_Escape)
    _pump(2)
    assert not dlg.isVisible() and not ul.moving and (ul.x, ul.y) == (40.0, 30.0)
    import ctgedit

    with open(
        os.path.join(os.path.dirname(ctgedit.__file__), "help", "pages", "underlay.md"), encoding="utf-8"
    ) as f:
        manual = f.read()
    assert "The keys work on the page, not in the Underlay window" in manual


def test_a_z_tap_while_moving_the_underlay_picks_the_zoom_box_and_ends_the_move(tmp_path, mixer, monkeypatch):
    """Z tapped (not held) while Move with the Mouse is on: the Zoom Box tool, and the move
    ends where the underlay is (as when any other tool is picked); Z held and let go keeps
    the move's open hand at once (not the arrow until the pointer moves)."""
    w, v = mixer, mixer.view
    ul = _underlay_image(w, _picture(tmp_path / "ref.png", 300, 200), monkeypatch)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(400, 300))
    _pump(2)
    w.act_underlay_move.trigger()
    _drag(v, QtCore.QPoint(300, 300), QtCore.QPoint(340, 330), through_filters=True)
    assert ul.moving and (ul.x, ul.y) == (40.0, 30.0)

    def key(kind, k, text=""):
        QtWidgets.QApplication.sendEvent(v, QtGui.QKeyEvent(kind, k, NOMOD, text))

    # held and let go: Select, still moving, the open hand shown at once
    key(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, "z")
    v._zhold["t"] -= 1.0
    key(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, "z")
    _pump(1)
    assert v.tool() == "select" and ul.moving
    assert v.viewport().cursor().shape() == QtCore.Qt.OpenHandCursor
    # tapped: the Zoom Box tool, and the move ends where it is
    key(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, "z")
    key(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, "z")
    _pump(1)
    assert v.tool() == "zoom" and w.tool_actions["zoom"].isChecked()
    assert not ul.moving and not w.act_underlay_move.isChecked()
    assert (ul.x, ul.y) == (40.0, 30.0)
    assert w.status_underlay.text() == "Underlay 40 %"
    assert v.viewport().cursor().shape() == QtCore.Qt.CrossCursor


def test_a_scroll_at_150_percent_draws_the_background_where_a_full_repaint_does(tmp_path):
    """At a fractional screen scale Qt moves the cached background by whole device pixels:
    the canvas draws it again after such a scroll, so the underlay (and the grid, the page
    edge) never drifts from the objects."""
    import subprocess
    import sys

    code = r"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1.5"
from PySide6 import QtCore, QtGui, QtWidgets
app = QtWidgets.QApplication([])
from ctgedit.ui.editor import canvas_hooks
from ctgedit.ui.editor.canvas import CanvasView

def lines(view, painter, exposed):
    painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 0))
    x = int(exposed.left()) // 7 * 7
    while x < exposed.right():
        painter.drawLine(QtCore.QLineF(x, exposed.top(), x, exposed.bottom()))
        x += 7
canvas_hooks.register_overlay("test_lines", lines, layer="background")
scene = QtWidgets.QGraphicsScene(0, 0, 3000, 2000)
v = CanvasView()
v.setScene(scene)
v.resize(400, 300)
v.show()
v.set_zoom(2.0)
app.processEvents()
assert v.viewport().devicePixelRatioF() == 1.5, v.viewport().devicePixelRatioF()
bar = v.horizontalScrollBar()
bar.setValue(600)
v.viewport().repaint()
for _ in range(6):
    bar.setValue(bar.value() + 1)
    v.viewport().repaint()
a = v.viewport().grab().toImage()
v.resetCachedContent()
v.viewport().repaint()
b = v.viewport().grab().toImage()
print("SAME" if a == b else "DIFFERENT")
"""
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120, cwd=os.getcwd()
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().endswith("SAME"), (r.stdout, r.stderr[-1500:])
