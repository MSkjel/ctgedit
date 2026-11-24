"""The window pictures Cicode asks for (WinFile, WinPrint, WinCopy): ctsim/imageWanted,
answered with ctsim/windowImage."""

import base64
import gc
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, wait_for  # noqa: E402
from qtkit import app as _app  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")


@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    return folder


@pytest.fixture
def page_file(tmp_path):
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    path = proj / "FxSim1.ctg"
    path.write_bytes(b"")
    return str(path)


@pytest.fixture
def hub():
    h = C.Hub(linger=0)
    yield h
    h.shutdown()


def _answer(srv, image_id):
    return next((a for a in getattr(srv, "images", ()) if a.get("id") == image_id), None)


def test_the_viewer_draws_the_window_cicode_asks_for(servers, hub, page_file):
    """The page (400 x 300, white, a red rectangle at 100, 100, 80 x 60) is drawn at the scale
    asked as a bitmap file; WinCopy's picture goes to the clipboard; a window no tab shows
    gets 'none'."""
    app = _app()
    from PySide6 import QtGui
    from ctgedit.ui.simulate.window import SimulateWindow

    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    win = SimulateWindow()
    win.show()
    pump = app.processEvents
    try:
        sim = _sim(hub)
        win.open_page(page_file, page=_rect_page(), sim=sim)
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        hello = next(c.hello for c in srv.conns if (c.hello or {}).get("images"))
        assert hello["images"] is True
        w0 = sim.window
        sp = win.current()
        sp.canvas.set_zoom(0.6)  # (the picture does not follow the zoom)
        pump()
        srv.want_image(
            {
                "id": 5,
                "window": w0,
                "page": "Mixer",
                "function": "WinFile",
                "xScale": 0.5,
                "yScale": 1.0,
                "clipboard": False,
            }
        )
        assert wait_for(lambda: (win.tick(), _answer(srv, 5) is not None)[1], pump=pump)
        data = base64.b64decode(_answer(srv, 5)["bmp"])
        assert data[:2] == b"BM" and int.from_bytes(data[28:30], "little") == 24
        img = QtGui.QImage.fromData(data, "BMP")
        assert (img.width(), img.height()) == (200, 300)
        # the rectangle (its colour animated), half as wide: x 50 to 90, y 100 to 160
        inside = QtGui.QColor(img.pixel(55, 105)).name()
        assert inside != "#ffffff" and QtGui.QColor(img.pixel(85, 155)).name() == inside
        for x, y in ((45, 130), (95, 130), (70, 95), (70, 165), (10, 10)):
            assert QtGui.QColor(img.pixel(x, y)).name() == "#ffffff", (x, y)
        # WinCopy: the picture the simulator sends back goes to the clipboard
        clip = QtGui.QGuiApplication.clipboard()
        clip.clear()
        srv.want_image(
            {
                "id": 6,
                "window": w0,
                "page": "Mixer",
                "function": "WinCopy",
                "xScale": 1.0,
                "yScale": 1.0,
                "clipboard": True,
            }
        )
        assert wait_for(lambda: (win.tick(), _answer(srv, 6) is not None)[1], pump=pump)
        assert wait_for(
            lambda: (win.tick(), clip.image().width() == 400 and clip.image().height() == 300)[1], pump=pump
        )
        # a window no tab shows: none
        srv.want_image({"id": 7, "window": 42, "page": "X", "function": "WinFile", "xScale": 1, "yScale": 1})
        assert wait_for(lambda: (win.tick(), _answer(srv, 7) is not None)[1], pump=pump)
        assert _answer(srv, 7) == {"id": 7, "none": True}
    finally:
        win.close()
        win.deleteLater()
        pump()
        srv.stop()
        gc.collect()
