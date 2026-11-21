"""Colours and pictures that flash on their own."""

import os

import pytest

#: the region of FxTplFlash the template's art covers, inside the page edge the editor
#: draws (the frame's top left corner at 10,10, the text at 20,20, the lines at y 50 and
#: 60..120 from x 20 to 400, the icon at 500,20)
REGION = (4, 4, 556, 126)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def page(fx_page):
    from ctgedit.io.reader import read_page

    return read_page(fx_page("FxTplFlash"))


def _records(page):
    """(frame, text, line1, line2, icon): the template's records of FxTplFlash, found by
    their kind and caption (they carry no names)."""
    objs = page.objects
    frame = [i for i, o in enumerate(objs) if o.kind.value == "rectangle" and o.parent is not None]
    text = [i for i, o in enumerate(objs) if o.style.text == "FxFlashText"]
    lines = [i for i, o in enumerate(objs) if o.kind.value == "line"]
    icon = [i for i, o in enumerate(objs) if o.kind.value == "symbol"]
    assert len(frame) == len(text) == len(icon) == 1 and len(lines) == 2
    return frame[0], text[0], lines[0], lines[1], icon[0]


def _scene(qapp, page):
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(page)
    return sc


def _grab(sc):
    from PySide6 import QtCore, QtGui

    x, y, w, h = REGION
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(0xFFFFFFFF)
    p = QtGui.QPainter(img)
    sc.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(x, y, w, h))
    p.end()
    return img


def _count(img, rgb):
    return sum(
        1 for y in range(img.height()) for x in range(img.width()) if img.pixel(x, y) & 0xFFFFFF == rgb
    )


def test_the_reader_keeps_the_flashing_pairs_and_the_second_picture(page):
    objs = page.objects
    FRAME, TEXT, LINE1, LINE2, ICON = _records(page)
    for i in (FRAME, TEXT, LINE1, LINE2):
        slots = objs[i].raw.get("_flash_slots")
        assert slots is not None, i
        assert (0xFE8787, 0xFF0000) in slots, (i, slots)
    assert objs[ICON].raw.get("_embedded_bmp_flash") is not None
    # a record whose colours do not flash keeps none
    assert sum(1 for o in objs if o.raw.get("_flash_slots")) == 4
    steady = [i for i, o in enumerate(objs) if o.kind.value == "rectangle" and o.parent is None]
    assert len(steady) == 1 and not objs[steady[0]].raw.get("_flash_slots")


def test_the_overlay_alternates_with_the_flash_phase(qapp, page):
    sc = _scene(qapp, page)
    on = _grab(sc)
    assert sc.set_flash_phase(False) >= 1  # the flashing icon
    off = _grab(sc)
    assert on != off
    # the frame, the text and the X: light red in the on phase, red in the off phase
    assert _count(off, 0xFF0000) > _count(on, 0xFF0000) + 500
    assert _count(on, 0xFE8787) > _count(off, 0xFE8787) + 500
    sc.set_flash_phase(True)
    assert _grab(sc) == on  # the on phase is the static look again


def test_an_export_draws_the_static_look_in_the_off_phase(qapp, page):
    sc = _scene(qapp, page)
    on = _grab(sc)
    sc.set_flash_phase(False)
    with sc.export_only(None):
        assert _grab(sc) == on
    assert _grab(sc) != on  # back to the off phase after it


def test_a_colour_the_simulator_sends_wins_over_the_flash(qapp, page):
    from ctgedit.sim.state import ObjectState

    FRAME = _records(page)[0]
    sc = _scene(qapp, page)
    an = page.objects[FRAME].raw["_handle"] + 1
    sc.apply_states({an: ObjectState(an, fill_colour=0x0000FF)})
    sc.set_flash_phase(False)
    layer = sc.sim_layer()
    look = layer.look_of(FRAME)
    assert look.flash and look.colour == 0x0000FF
    assert layer._snaps[(FRAME, "main")].flashed is None
    sc.apply_states({an: None})  # back to its own colours: it flashes
    assert layer._snaps[(FRAME, "main")].flashed


def test_a_phase_change_alone_is_a_page_state():
    """The client hands the window a PageState when only the flash phase changed (the engine
    sends a pageChanged with no objects then)."""
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation

    sim = EngineSimulation.__new__(EngineSimulation)
    import threading

    sim._lock = threading.RLock()
    sim._live, sim._full, sim._changes = True, None, {}
    sim._drawn_next = sim._controls_next = None
    sim._replace_next = False
    sim._window, sim.page_name, sim._page_serial = 0, "FxTplFlash", 0
    sim._flash, sim._flash_sent = True, True
    sim._poll_params = lambda: None
    assert sim.poll() is None
    sim._flash = False
    st = sim.poll()
    assert st is not None and st.flash_on is False and not st.objects
    assert sim.poll() is None
