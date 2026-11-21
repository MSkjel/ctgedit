"""Embedded controls run in the control host: their pictures on the canvas, their properties.

The host itself (ctgedit/native/control_host.cs) is replaced by a fake here; the live
check of the real one with the installed controls is tests_live/test_controlhost_live.py.
"""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.backend import controlhost as H  # noqa: E402
from ctgedit.format import factory, olecontrol  # noqa: E402
from ctgedit.io.pgregister import ole_clsid  # noqa: E402
from qtkit import app as _app

KINDS = ("activex", "process_analyst", "scheduler")


def _doc(fx_install):
    from ctgedit.editing import EditableDocument

    dst = os.path.join(fx_install.project("FxMain"), "FxControls.ctg")
    shutil.copy2(fx_install.page("FxShapes"), dst)
    doc = EditableDocument.open(dst)
    doc.derive_check = True
    return doc


def _grown(state: bytes) -> bytes:
    """The same compound file with one more (empty) sector: a new state of another size."""
    return state + b"\0" * 512


class _FakeHost:
    """Stands in for ctgctl.exe: 'draw' writes a picture (or says there is none), 'edit'
    writes the state it was given grown by a sector (or cancels / fails)."""

    def __init__(self, picture=True, edit="ok"):
        self.picture, self.edit, self.calls = picture, edit, []

    def __call__(self, args, timeout):
        import subprocess

        self.calls.append(list(args))
        mode, _clsid, state, out = args[0], args[1], args[2], args[3]
        if mode == "draw":
            if not self.picture:
                return subprocess.CompletedProcess(args, H.NO_PICTURE, "", "")
            img = QtGui.QImage(int(args[4]), int(args[5]), QtGui.QImage.Format_RGB32)
            img.fill(QtGui.QColor(10, 120, 200))
            img.save(out, "PNG")
            return subprocess.CompletedProcess(args, H.DONE, "", "")
        if self.edit == "cancel":
            return subprocess.CompletedProcess(args, H.CANCELLED, "", "")
        if self.edit == "fail":
            return subprocess.CompletedProcess(args, H.FAILED, "", "Class not registered")
        with open(state, "rb") as f:
            data = f.read()
        with open(out, "wb") as f:
            f.write(_grown(data))
        return subprocess.CompletedProcess(args, H.DONE, "", "")


@pytest.fixture
def fake_host(monkeypatch):
    fake = _FakeHost()
    monkeypatch.setattr(H, "_run", fake)
    monkeypatch.setattr(H, "available", lambda: True)
    return fake


# ------------------------------------------------------------------ the saved state
@pytest.mark.parametrize("kind", KINDS)
def test_the_saved_state_is_replaced_and_the_rest_of_the_record_kept(kind):
    po = factory.create_object(kind, (0, 0, 160, 120), handle=7)
    state = olecontrol.storage(po)
    assert state[:8] == olecontrol.CFB_SIGNATURE and ole_clsid(po.body_raw)
    before = po.body_raw
    start, end = olecontrol.storage_span(po)
    head, tail = before[: start - 4], before[end:]
    assert olecontrol.replace_storage(po, _grown(state))
    assert olecontrol.storage(po) == _grown(state)
    s2, e2 = olecontrol.storage_span(po)
    assert po.body_raw[: s2 - 4] == head and po.body_raw[e2:] == tail
    assert not olecontrol.replace_storage(po, _grown(state))  # the same: no change
    assert olecontrol.replace_storage(po, state) and po.body_raw == before
    with pytest.raises(ValueError):
        olecontrol.replace_storage(po, b"not a compound file")


def test_a_new_state_is_one_undo_step_and_survives_a_save(fx_install):
    from ctgedit.editing import EditableDocument

    doc = _doc(fx_install)
    a = doc.create_object("process_analyst", rect=(20, 20, 420, 260))
    clsid, state, size = doc.control_state(a)
    assert clsid == ole_clsid(doc.parsed.page.objects[a].body_raw) and size == (400, 240)
    n = len(doc._undo)
    assert doc.set_control_state(a, _grown(state)) is True
    assert len(doc._undo) == n + 1
    assert doc.set_control_state(a, _grown(state)) is False  # the same state: nothing
    doc.save()
    again = EditableDocument.open(doc.path)
    assert again.control_state(a)[1] == _grown(state)
    doc.undo()
    assert doc.control_state(a)[1] == state


# ------------------------------------------------------------------ the host
def test_a_picture_is_taken_once_and_kept_on_disk(fake_host):
    po = factory.create_object("activex", (0, 0, 160, 120), handle=7)
    clsid, state = ole_clsid(po.body_raw), olecontrol.storage(po)
    png = H.take_picture(clsid, state, 160, 120)
    assert png and os.path.isfile(png)
    assert H.take_picture(clsid, state, 160, 120) == png
    H._PICTURES.clear()  # (the next start: the picture is still on disk)
    assert H.cached_picture(H.picture_key(clsid, state, 160, 120)) == png
    assert len([c for c in fake_host.calls if c[0] == "draw"]) == 1
    # another size is another picture
    assert H.take_picture(clsid, state, 200, 120) != png


def test_a_control_that_paints_nothing_has_no_picture(fake_host):
    fake_host.picture = False
    po = factory.create_object("scheduler", (0, 0, 160, 120), handle=7)
    clsid, state = ole_clsid(po.body_raw), olecontrol.storage(po)
    assert H.take_picture(clsid, state, 160, 120) == ""
    H._PICTURES.clear()
    assert H.cached_picture(H.picture_key(clsid, state, 160, 120)) == ""
    assert len(fake_host.calls) == 1


def test_edit_returns_the_new_state_none_on_cancel_and_says_why_it_failed(fake_host):
    po = factory.create_object("activex", (0, 0, 160, 120), handle=7)
    clsid, state = ole_clsid(po.body_raw), olecontrol.storage(po)
    assert H.edit(clsid, state, 160, 120, owner=1234) == _grown(state)
    assert fake_host.calls[-1][-1] == 1234  # the window it is owned by
    fake_host.edit = "cancel"
    assert H.edit(clsid, state, 160, 120) is None
    fake_host.edit = "fail"
    with pytest.raises(H.ControlHostError, match="Class not registered"):
        H.edit(clsid, state, 160, 120)


# ------------------------------------------------------------------ the canvas
def test_the_canvas_shows_the_picture_once_it_is_taken(fx_install, fake_host, monkeypatch):
    _app()
    from ctgedit.render.control_pictures import ControlItem
    from ctgedit.render.scene import PageScene

    monkeypatch.delenv("CTG_NO_CONTROL_HOST", raising=False)
    doc = _doc(fx_install)
    pa = doc.create_object("process_analyst", rect=(20, 20, 420, 260))
    scene = PageScene()
    scene.set_page(doc.page)
    item = scene._items[pa]
    assert isinstance(item, ControlItem)
    deadline = QtCore.QDeadlineTimer(10000)
    while not item.has_picture() and not deadline.hasExpired():
        QtWidgets.QApplication.processEvents()
        QtCore.QThread.msleep(20)
    assert item.has_picture()
    # the page drawn: the control's box is its picture, from its first pixel to its last
    img = QtGui.QImage(500, 300, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    scene.render(p, QtCore.QRectF(0, 0, 500, 300), QtCore.QRectF(0, 0, 500, 300))
    p.end()
    for x, y in ((20, 20), (200, 140), (419, 259)):
        assert img.pixelColor(x, y) == QtGui.QColor(10, 120, 200), (x, y)
    assert img.pixelColor(19, 19) != QtGui.QColor(10, 120, 200)


def test_the_tests_run_no_control_host(fx_install):
    _app()
    from ctgedit.render.scene import PageScene

    assert os.environ.get("CTG_NO_CONTROL_HOST") == "1"
    doc = _doc(fx_install)
    a = doc.create_object("activex", rect=(20, 20, 180, 140))
    scene = PageScene()
    scene.set_page(doc.page)
    assert scene._items[a].key == ""  # the placeholder box only


# ------------------------------------------------------------------ Control Properties
def test_control_properties_stores_what_the_control_returns(fx_install, fake_host):
    _app()
    from ctgedit.ui.objprops import ObjectPropertiesDialog
    from ctgedit.ui.objprops import activex as AXP

    doc = _doc(fx_install)
    a = doc.create_object("activex", rect=(400, 400, 560, 560))
    state = doc.control_state(a)[1]
    dlg = ObjectPropertiesDialog(doc, a)
    n = len(doc._undo)
    assert AXP.edit_control(dlg) is True
    assert doc.control_state(a)[1] == _grown(state) and len(doc._undo) == n + 1
    fake_host.edit = "cancel"
    assert AXP.edit_control(dlg) is False and len(doc._undo) == n + 1
    dlg.reject()


def test_the_setting_off_shows_the_boxes(fx_install, fake_host, monkeypatch):
    """Settings > Canvas > Draw embedded controls as they look, off: no picture is taken,
    and a picture already taken is not drawn."""
    _app()
    from ctgedit.render import control_pictures as CP
    from ctgedit.render.scene import PageScene

    monkeypatch.delenv("CTG_NO_CONTROL_HOST", raising=False)
    monkeypatch.setattr(CP, "_ENABLED", [False])
    doc = _doc(fx_install)
    a = doc.create_object("activex", rect=(20, 20, 180, 140))
    scene = PageScene()
    scene.set_page(doc.page)
    assert scene._items[a].key == "" and not fake_host.calls
    CP.set_enabled(True)
    scene.set_page(doc.page)
    item = scene._items[a]
    deadline = QtCore.QDeadlineTimer(10000)
    while not item.has_picture() and not deadline.hasExpired():
        QtWidgets.QApplication.processEvents()
        QtCore.QThread.msleep(20)
    assert item.has_picture()
    CP.set_enabled(False)
    assert not CP.enabled()
