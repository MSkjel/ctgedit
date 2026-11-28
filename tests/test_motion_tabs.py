"""Movement, Scaling, Rotation and Slider pages."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app


@pytest.fixture
def MIXER(fx_page):
    """The host page of the new objects (FxShapes of the fake install copy)."""
    return fx_page("FxShapes")


@pytest.fixture
def MOTION(fx_page):
    """FxMotion of the fake install copy: the stored motion keys."""
    return fx_page("FxMotion")


def _named(doc, name):
    """The index of the object named ``name``."""
    (i,) = doc.find_objects_by_name(name)
    return i


@pytest.fixture(autouse=True)
def _derive_check(monkeypatch):
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")


def _copy(tmp_path, src):
    dst = tmp_path / os.path.basename(src)
    shutil.copy2(src, dst)
    return str(dst)


def _sheet(doc, index):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index)


def _w(dlg, key, n=0):
    return dlg._widgets[key][n][1]


def _axis(dlg, family="rot", n=0):
    return dlg.motion_editor.axis_picker(family, n)


def _rect(doc, rect=(5, 5, 80, 60)):
    return doc.create_object("rectangle", rect=rect, colour=0x336699)


# ------------------------------------------------------------------ stored values shown
def test_fixtures_show_their_offsets_angles_and_axes(MOTION):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(MOTION)
    # rotation about the BOTTOM centre (express ROTYORG 1, no ROTMODE)
    dlg = _sheet(doc, _named(doc, "FxRotExpress"))
    assert _w(dlg, "ROT").text() == "FX_SPEED"
    assert (_w(dlg, "ROTMINPC").value(), _w(dlg, "ROTMAXPC").value()) == (0, 135)
    p = _axis(dlg)
    assert p.state() == (False, 0, 1) and p.rb_express.isChecked()
    assert p.grid.isVisibleTo(p) and not p.custom.isVisibleTo(p)
    assert p.summary_text() == "Turns about the bottom centre."
    # the rotational slider page shows the SAME axis (shared keys)
    assert _axis(dlg, n=1).state() == (False, 0, 1)
    # a rotational slider about a CUSTOM point 30 px right of and 43 px below the centre
    dlg = _sheet(doc, _named(doc, "FxRotSlider"))
    # (its ROTSLMODE '1' has no continuous bit 4096: the box reads unticked; FxSliderHV's
    # MVXSLMODE 4097 has it)
    assert _w(dlg, "ROTSL").text() == "FX_SPEED" and not _w(dlg, "ROTSLMODE").isChecked()
    hv = _sheet(doc, _named(doc, "FxSliderHV"))
    assert _w(hv, "MVXSL").text() == "FX_SPEED" and _w(hv, "MVXSLMODE").isChecked()
    p = _axis(dlg, n=1)
    assert p.state() == (True, 30, 43) and p.rb_custom.isChecked()
    assert p.custom.isVisibleTo(p) and not p.grid.isVisibleTo(p)
    assert (p.sp_x.value(), p.sp_y.value()) == (30, 43)
    assert "30 px right" in p.summary_text() and "43 px down" in p.summary_text()

    dlg = _sheet(doc, _named(doc, "FxRotBoom"))  # a crane boom: custom (168, -3), 0..45
    assert _w(dlg, "ROT").text() == "FX_LEVEL1"
    assert (_w(dlg, "ROTMINPC").value(), _w(dlg, "ROTMAXPC").value()) == (0, 45)
    assert _axis(dlg).state() == (True, 168, -3)
    assert "168 px right and 3 px up" in _axis(dlg).summary_text()
    dlg = _sheet(doc, _named(doc, "FxRotClaw"))  # a claw: a NEGATIVE max = anticlockwise
    assert _w(dlg, "ROTMAXPC").value() == -90 and _axis(dlg).state() == (True, 40, 0)
    dlg = _sheet(doc, _named(doc, "FxRotTop"))  # express top centre
    assert _axis(dlg).state() == (False, 0, -1)

    dlg = _sheet(doc, _named(doc, "FxSzxGrow"))  # scaled from the LEFT edge
    assert _axis(dlg, "sx").state() == (False, -1, 0)
    assert "left edge stays" in _axis(dlg, "sx").summary_text()
    assert (_w(dlg, "SZXMINPC").value(), _w(dlg, "SZXMAXPC").value()) == (0, 400)
    dlg = _sheet(doc, _named(doc, "FxSzyMove"))  # SZYORG -1 + a table scroll
    assert _axis(dlg, "sy").state() == (False, 0, -1)
    # CtDraw stores the typed number as it is: MVYMAXPT -4000 moves DOWN at maximum
    assert (_w(dlg, "MVYMINPT").value(), _w(dlg, "MVYMAXPT").value()) == (0, -4000)

    dlg = _sheet(doc, _named(doc, "FxSzyCustom"))  # SZYMODE 1: a custom axis 65 px down
    assert _axis(dlg, "sy").state() == (True, 0, 65)
    assert _axis(dlg, "sy").sp_x is None


def test_axis_state_and_edits_follow_ctdraws_page():
    """(load) / (apply): Express = no mode key and
    a 0 stored as NO key; Custom = mode '1' and both offsets always."""
    from ctgedit.ui.objprops import motion as MO

    rot, sx, sy = MO.AXES["rot"], MO.AXES["sx"], MO.AXES["sy"]
    assert MO.axis_state({}, rot) == ((False, 0, 0), "")
    assert MO.axis_state({"ROTYORG": "1"}, rot) == ((False, 0, 1), "")
    assert MO.axis_state({"ROTXORG": "5"}, rot) == ((False, 0, 0), "")  # reads centre
    assert MO.axis_state({"ROTMODE": "1", "ROTXORG": "-20", "ROTYORG": "-20"}, rot) == ((True, -20, -20), "")
    assert MO.axis_state({"ROTMODE": "1", "ROTXORG": "%dx%"}, rot)[1] == "%dx%"
    assert MO.axis_state({"SZXMODE": "1", "SZXORG": "20"}, sx) == ((True, 20, 0), "")
    assert MO.axis_edits(rot, (False, 0, 1)) == [("ROTMODE", None), ("ROTXORG", None), ("ROTYORG", "1")]
    assert MO.axis_edits(rot, (True, 0, 0)) == [("ROTMODE", "1"), ("ROTXORG", "0"), ("ROTYORG", "0")]
    assert MO.axis_edits(sx, (False, -1, 0)) == [("SZXMODE", None), ("SZXORG", "-1")]
    assert MO.axis_edits(sy, (True, 0, -7)) == [("SZYMODE", "1"), ("SZYORG", "-7")]


# ------------------------------------------------------------------ companion defaults
def test_a_new_expression_writes_ctdraws_companion_keys(MIXER, tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, MIXER))
    i = _rect(doc)
    dlg = _sheet(doc, i)
    assert _w(dlg, "MVXMINPT").key_value() is None  # '(not set)' until switched on
    _w(dlg, "MVX").setText("Level_1")
    # the fields show what will be stored: CtDraw's defaults
    assert (_w(dlg, "MVXMINPT").value(), _w(dlg, "MVXMAXPT").value()) == (0, 500)
    assert _w(dlg, "MVXMAXPT", 1).value() == 500  # the H slider shares them
    _w(dlg, "ROT").setText("Angle_1")
    _w(dlg, "SZY").setText("Level_2")
    _w(dlg, "MVY").setText("Level_3")
    _w(dlg, "MVY").setText("")  # switched off again: no keys
    n = len(doc._undo)
    assert dlg.apply()
    assert len(doc._undo) == n + 1
    rp = doc.object_properties(i)
    assert (rp["MVX"], rp["MVXMINPT"], rp["MVXMAXPT"]) == ("Level_1", "0", "500")
    assert (rp["ROT"], rp["ROTMINPC"], rp["ROTMAXPC"]) == ("Angle_1", "0", "360")
    assert (rp["SZY"], rp["SZYMINPC"], rp["SZYMAXPC"]) == ("Level_2", "0", "100")
    # a new rotation / scaling about the centre stores no axis key (CtDraw
    # removes an express 0), and nothing of the movement switched off again
    assert not {"ROTMODE", "ROTXORG", "ROTYORG", "SZYMODE", "SZYORG"} & set(rp)
    assert not {"MVY", "MVYMINPT", "MVYMAXPT"} & set(rp)
    # the object is dynamic now (the register writes its pgdynobj row)
    assert doc.parsed.page.objects[i].subtype_size & 0x10
    # a value typed by hand is kept, not replaced by the default
    j = _rect(doc, (100, 5, 180, 60))
    dlg = _sheet(doc, j)
    _w(dlg, "SZXMAXPC").setValue(250)
    _w(dlg, "SZX").setText("Level_1")
    assert _w(dlg, "SZXMAXPC").value() == 250 and _w(dlg, "SZXMINPC").value() == 0
    assert dlg.apply()
    rp = doc.object_properties(j)
    assert (rp["SZXMINPC"], rp["SZXMAXPC"]) == ("0", "250")
    # a slider tag fills the shared offsets too
    k = _rect(doc, (200, 5, 280, 60))
    dlg = _sheet(doc, k)
    _w(dlg, "MVYSL").setText("Loop_1_SP")
    assert dlg.apply()
    rp = doc.object_properties(k)
    assert (rp["MVYSL"], rp["MVYSLMODE"], rp["MVYMINPT"], rp["MVYMAXPT"]) == ("Loop_1_SP", "1", "0", "500")
    doc.undo()
    assert not {"MVYSL", "MVYMINPT", "MVYMAXPT"} & set(doc.object_properties(k))


# ------------------------------------------------------------------ the axis picker
def test_center_axis_express_and_custom_write_ctdraws_keys(MIXER, tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, MIXER))
    i = _rect(doc)
    dlg = _sheet(doc, i)
    _w(dlg, "ROT").setText("Angle_1")
    p = _axis(dlg)
    # a click on the grid's bottom-centre point picks it
    QtTest.QTest.mouseClick(p.grid, QtCore.Qt.LeftButton, pos=p.grid.pos_of((0, 1)).toPoint())
    assert p.state() == (False, 0, 1)
    assert _axis(dlg, n=1).state() == (False, 0, 1)  # the slider page follows
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert rp["ROTYORG"] == "1" and "ROTXORG" not in rp and "ROTMODE" not in rp
    # the arrow keys walk the points: bottom centre -> bottom left -> middle left
    p.grid.setFocus()
    QtTest.QTest.keyClick(p.grid, QtCore.Qt.Key_Left)
    QtTest.QTest.keyClick(p.grid, QtCore.Qt.Key_Up)
    assert p.state() == (False, -1, 0)
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert rp["ROTXORG"] == "-1" and "ROTYORG" not in rp
    # Custom: the offsets right / down of the centre, both stored, 0 too
    p.rb_custom.setChecked(True)
    p.sp_x.setValue(8)
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert (rp["ROTMODE"], rp["ROTXORG"], rp["ROTYORG"]) == ("1", "8", "0")
    p.sp_y.setValue(-13)
    assert dlg.apply() and doc.object_properties(i)["ROTYORG"] == "-13"
    # back to the express centre: every axis key gone
    p.rb_express.setChecked(True)
    p.grid.set_point((0, 0), emit=True)
    assert dlg.apply()
    assert not {"ROTMODE", "ROTXORG", "ROTYORG"} & set(doc.object_properties(i))
    assert dlg.apply() is False  # nothing pending
    # scaling: the left edge / a custom line below the middle
    _w(dlg, "SZX").setText("Level_1")
    _axis(dlg, "sx").grid.set_point((-1, 0), emit=True)
    _w(dlg, "SZY").setText("Level_2")
    ay = _axis(dlg, "sy")
    ay.rb_custom.setChecked(True)
    ay.sp_y.setValue(20)
    assert "20 px below the middle" in ay.summary_text()
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert rp["SZXORG"] == "-1" and "SZXMODE" not in rp
    assert (rp["SZYMODE"], rp["SZYORG"]) == ("1", "20")
    # the register (pgdynobj) carries the columns the compiler reads
    from ctgedit.format import geometry as G
    from ctgedit.io import pgregister

    an = str(G.live_an(doc.parsed.page.objects[i]))
    row = next(r for r in pgregister.page_rows(doc.serialize(), "Mixer")["pgdynobj"] if r["AN"] == an)
    assert (row["SZXORG"], row["SZYMODE"], row["SZYORG"], row["ROTMAXPC"]) == ("-1", "1", "20", "360")


def test_clear_property_resets_the_axis_unless_the_slider_shares_it(MIXER, tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import motion as MO

    doc = EditableDocument.open(_copy(tmp_path, MIXER))
    i = _rect(doc)
    for k, v in (
        ("ROT", "Angle_1"),
        ("ROTMINPC", "0"),
        ("ROTMAXPC", "90"),
        ("ROTMODE", "1"),
        ("ROTXORG", "4"),
        ("ROTYORG", "9"),
    ):
        assert doc.ensure_property(i, k, v)
    dlg = _sheet(doc, i)
    assert _axis(dlg).state() == (True, 4, 9)
    dlg._clear(MO.MOVE_ROT)
    assert _axis(dlg).state() == (False, 0, 0) and _axis(dlg, n=1).state() == (False, 0, 0)
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert not {"ROT", "ROTMINPC", "ROTMAXPC", "ROTMODE", "ROTXORG", "ROTYORG"} & set(rp)
    # the rotational slider keeps the axis a cleared rotation shares with it
    j = _rect(doc, (100, 5, 180, 60))
    for k, v in (
        ("ROTSL", "Loop_1_SP"),
        ("ROTSLMODE", "1"),
        ("ROTMINPC", "0"),
        ("ROTMAXPC", "90"),
        ("ROTYORG", "1"),
    ):
        assert doc.ensure_property(j, k, v)
    dlg = _sheet(doc, j)
    dlg._clear(MO.MOVE_ROT)
    assert _axis(dlg).state() == (False, 0, 1)
    assert dlg.apply() is False
    assert doc.object_properties(j)["ROTYORG"] == "1"
    # a custom offset the page cannot show is kept, and Clear removes it
    k = _rect(doc, (200, 5, 280, 60))
    for key, v in (("ROT", "Angle_1"), ("ROTMODE", "1"), ("ROTXORG", "%dx%")):
        assert doc.ensure_property(k, key, v)
    dlg = _sheet(doc, k)
    assert not _axis(dlg).rb_custom.isEnabled() and "%dx%" in _axis(dlg).toolTip()
    _w(dlg, "ROT").setText("Angle_2")
    assert dlg.apply() and doc.object_properties(k)["ROTXORG"] == "%dx%"
    dlg._clear(MO.MOVE_ROT)
    assert dlg.apply()
    assert not {"ROTMODE", "ROTXORG"} & set(doc.object_properties(k))


def test_an_axis_the_object_cannot_keep_stays_locked_after_apply(MIXER, tmp_path, monkeypatch):
    """An axis the record cannot keep (a placed genie root: CtDraw's page load
    drops it) is read-only on BOTH rotation pages -- the slider page's picker
    must not unlock the movement page's -- also after an Apply reloads the
    sheet, and nothing is written for it."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, MIXER))
    i = _rect(doc)
    real = doc._genie_root_drops
    monkeypatch.setattr(
        doc, "_genie_root_drops", lambda idx, key: (idx == i and key.upper() == "ROTMODE") or real(idx, key)
    )
    dlg = _sheet(doc, i)
    for n in (0, 1):
        assert not _axis(dlg, n=n).rb_express.isEnabled(), n
        assert "genie definition" in _axis(dlg, n=n).toolTip()
    _axis(dlg).grid.set_point((0, 1), emit=True)
    _w(dlg, "MVX").setText("Level_1")
    assert dlg.apply()
    assert not {"ROTMODE", "ROTXORG", "ROTYORG"} & set(doc.object_properties(i))
    for n in (0, 1):
        assert not _axis(dlg, n=n).rb_express.isEnabled(), n


# ------------------------------------------------------------------ movement / slider
def test_movement_and_slider_exclude_each_other_as_you_type(MIXER, tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, MIXER))
    i = _rect(doc)
    dlg = _sheet(doc, i)
    assert dlg.page_widget("slider_h").isEnabled()
    _w(dlg, "MVX").setText("Level_1")  # not applied yet
    assert not dlg.page_widget("slider_h").isEnabled()
    assert dlg.tab_marked("Movement", "Horizontal")
    _w(dlg, "MVX").setText("")
    assert dlg.page_widget("slider_h").isEnabled()
    assert not dlg.tab_marked("Movement")
    # the rotational slider excludes the rotation AND both other sliders
    _w(dlg, "ROTSL").setText("Loop_1_SP")
    for pid in ("move_rot", "slider_h", "slider_v"):
        assert not dlg.page_widget(pid).isEnabled(), pid
    assert dlg.page_widget("move_h").isEnabled()
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert rp["ROTSLMODE"] == "1" and (rp["ROTMINPC"], rp["ROTMAXPC"]) == ("0", "360")
    assert not dlg.page_widget("slider_h").isEnabled()  # still, from the stored keys
    # Clear Property of the slider gives the pages back at once
    from ctgedit.ui.objprops import motion as MO

    dlg._clear(MO.SLIDER_ROT)
    assert dlg.page_widget("move_rot").isEnabled() and dlg.page_widget("slider_h").isEnabled()


def test_page_ranges_and_defaults_are_ctdraws():
    """CtDraw's 'At minimum / maximum' spins: offsets 0 / 500 px,
    sizes 0 / 100 %, angles 0 / 360 degrees limited to -360..360."""
    from ctgedit.ui.objprops import motion as MO

    f = {fl.key: fl for s in MO.SUBS for fl in s.fields}
    assert (f["MVXMINPT"].default, f["MVXMAXPT"].default) == (0, 500)
    assert (f["MVYMINPT"].default, f["MVYMAXPT"].default) == (0, 500)
    assert (f["SZXMINPC"].default, f["SZXMAXPC"].default) == (0, 100)
    assert (f["ROTMINPC"].default, f["ROTMAXPC"].default) == (0, 360)
    assert (f["ROTMINPC"].lo, f["ROTMAXPC"].hi) == (-360, 360)
    assert (f["MVXMINPT"].lo, f["SZXMAXPC"].hi) == (-32767, 32767)
    assert MO.companion_defaults("mvx", {"MVXMAXPT": "7"}) == [("MVXMINPT", "0")]
    for key, comps in MO.COMPANIONS.items():
        assert all(c in MO.COMPANION_DEFAULTS for c in comps), key


# ------------------------------------------------------------------ groups
def test_group_and_member_motion_are_stored_on_each_record(MIXER, tmp_path):
    """A group and its members combine their movements at runtime (help
    1172160): the editor stores each record's own keys; the register gives the
    member's pgdynobj row the group's AN."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G
    from ctgedit.io import pgregister

    doc = EditableDocument.open(_copy(tmp_path, MIXER))
    a = _rect(doc, (300, 300, 340, 340))
    b = _rect(doc, (360, 300, 400, 340))
    g = doc.group_objects([a, b])
    m = doc.page.objects[g].members[0]
    dlg = _sheet(doc, g)
    assert "move_v" in dlg.page_ids() and "scale_h" in dlg.page_ids()
    _w(dlg, "MVY").setText("Lift_Pos")
    assert dlg.apply()
    dlg = _sheet(doc, m)
    _w(dlg, "ROT").setText("Arm_Angle")
    _axis(dlg).grid.set_point((-1, -1), emit=True)
    assert dlg.apply()
    gp, mp = doc.object_properties(g), doc.object_properties(m)
    assert (gp["MVY"], gp["MVYMINPT"], gp["MVYMAXPT"]) == ("Lift_Pos", "0", "500")
    assert "ROT" not in gp and "MVY" not in mp
    assert (mp["ROT"], mp["ROTXORG"], mp["ROTYORG"]) == ("Arm_Angle", "-1", "-1")
    objs = doc.parsed.page.objects
    rows = {r["AN"]: r for r in pgregister.page_rows(doc.serialize(), "Mixer")["pgdynobj"]}
    g_an, m_an = str(G.live_an(objs[g])), str(G.live_an(objs[m]))
    assert rows[g_an]["MVY"] == "Lift_Pos" and rows[g_an]["GROUP"] == ""
    assert rows[m_an]["ROT"] == "Arm_Angle" and rows[m_an]["GROUP"] == g_an


# ------------------------------------------------------------------ the Properties panel
def test_panel_add_animation_and_axis_rows(MIXER, MOTION, tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels.properties import PropertyPanel

    _app()
    doc = EditableDocument.open(_copy(tmp_path, MIXER))
    i = _rect(doc)
    panel = PropertyPanel()
    panel.set_target(doc, [i])

    def add_combo():
        c = next(
            c for c in panel.widget().findChildren(QtWidgets.QComboBox) if c.objectName() == "addAnimation"
        )
        c.fill()  # (its entries come with its first use)
        assert c.itemText(0) == "Add animation…"
        return c

    combo = add_combo()
    labels = [combo.itemText(k) for k in range(combo.count())]
    for want in (
        "Movement (horizontal)",
        "Movement (vertical)",
        "Rotation",
        "Scaling (horizontal)",
        "Scaling (vertical)",
    ):
        assert want in labels
    n = len(doc._undo)
    panel._on_add_animation(i, combo, labels.index("Rotation"))
    assert len(doc._undo) == n + 1  # one undo step
    rp = doc.object_properties(i)
    assert (rp["ROT"], rp["ROTMINPC"], rp["ROTMAXPC"]) == ("", "0", "360")
    panel.refresh()
    labels = [add_combo().itemText(k) for k in range(add_combo().count())]
    assert "Rotation" not in labels  # already there
    # the axis row appears once an expression is set
    assert doc.set_property(i, "ROT", "Angle_1")
    panel.refresh()
    box = next(
        b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title() == "Movement / Scaling"
    )
    # (the angles are number fields: their short labels' long text is the tool tip)
    tips = {e.toolTip() for e in box.findChildren(QtWidgets.QLineEdit)} | {
        e.toolTip() for e in box.findChildren(QtWidgets.QAbstractSpinBox)
    }
    assert any("anticlockwise at the minimum" in t for t in tips)
    centre = next(c for c in box.findChildren(QtWidgets.QComboBox) if c.findText("Bottom centre") >= 0)
    assert centre.currentText() == "Centre"
    panel._on_axis_choice(i, "rot", centre, centre.findText("Bottom centre"), (False, 0, 0), "move_rot")
    assert doc.object_properties(i)["ROTYORG"] == "1"
    opened = []
    panel.editPage.connect(lambda idx, pid: opened.append((idx, pid)))
    panel.refresh()
    box = next(
        b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title() == "Movement / Scaling"
    )
    centre = next(c for c in box.findChildren(QtWidgets.QComboBox) if c.findText("Bottom centre") >= 0)
    assert centre.currentText() == "Bottom centre"
    panel._on_axis_choice(i, "rot", centre, centre.findText("Custom offset…"), (False, 0, 1), "move_rot")
    assert opened == [(i, "move_rot")]
    # a button has no rotation / scaling pages (mask 0x81078e0f): not offered
    b = doc.create_object("button", rect=(10, 100, 80, 130), text="Go")
    panel.set_target(doc, [b])
    labels = [add_combo().itemText(k) for k in range(add_combo().count())]
    assert "Movement (horizontal)" in labels and "Rotation" not in labels
    assert "Scaling (horizontal)" not in labels
    # a configured slider takes its axis's movement off the list
    assert doc.ensure_property(i, "MVXSL", "Loop_1_SP") and doc.ensure_property(i, "MVXSLMODE", "1")
    panel.set_target(doc, [i])
    labels = [add_combo().itemText(k) for k in range(add_combo().count())]
    assert "Movement (horizontal)" not in labels and "Movement (vertical)" in labels
    # a stored custom axis reads in words (the crane boom FxRotBoom: 168 right, 3 up)
    g = EditableDocument.open(MOTION)
    panel.set_target(g, [_named(g, "FxRotBoom")])
    box = next(
        b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title() == "Movement / Scaling"
    )
    centre = next(c for c in box.findChildren(QtWidgets.QComboBox) if c.findText("Bottom centre") >= 0)
    assert centre.currentText() == "Custom: 168 px right, 3 px up"


def test_panel_custom_offset_of_a_rotational_slider_opens_the_slider_page(MOTION):
    """A rotational slider owns the axis (its movement page is greyed): the
    panel's 'Custom offset…' opens Slider > Rotational."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels.properties import PropertyPanel

    _app()
    doc = EditableDocument.open(MOTION)
    sl = _named(doc, "FxRotSlider")
    panel = PropertyPanel()
    panel.set_target(doc, [sl])
    box = next(
        b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title() == "Movement / Scaling"
    )
    centre = next(c for c in box.findChildren(QtWidgets.QComboBox) if c.findText("Bottom centre") >= 0)
    assert centre.currentText() == "Custom: 30 px right, 43 px down"
    opened = []
    panel.editPage.connect(lambda idx, pid: opened.append((idx, pid)))
    centre.activated.emit(centre.findText("Custom offset…"))
    assert opened == [(sl, "slider_rot")]


def test_custom_offset_from_the_panel_opens_the_sheet_page(MOTION, tmp_path):
    """The panel's 'Custom offset…' (PropertyPanel.editPage) opens the
    object's sheet on the Rotational page."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(_copy(tmp_path, MOTION))
        boom = _named(w.doc, "FxRotBoom")
        w.props.editPage.emit(boom, "move_rot")
        dlg = w._dialogs.get(("objprops", boom))
        assert dlg is not None
        _p, tabs, k = next(t for t in dlg._pages if t[0].id == "move_rot")
        assert dlg._tabs.currentWidget() is tabs and tabs.currentIndex() == k
        assert dlg.motion_editor.axis_picker("rot").state() == (True, 168, -3)
        dlg.reject()
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.close()


# ------------------------------------------------------------------ the .ctF rotation sweep
def test_rotation_origin_is_fun_004e6f00():
    from ctgedit.format import ctf as CTF

    b = (10, 20, 31, 61)
    assert CTF.rotation_origin(b, 0, 0, 0) == (20, 40)  # C division: 41/2 = 20
    assert CTF.rotation_origin(b, 0, -1, -1) == (10, 20)
    assert CTF.rotation_origin(b, 0, 1, 1) == (31, 61)
    assert CTF.rotation_origin(b, 0, 0, 1) == (20, 61)  # express bottom centre
    assert CTF.rotation_origin(b, 0, 5, 7) == (20, 40)  # other values = centre
    assert CTF.rotation_origin(b, 1, 5, -7) == (25, 33)  # custom = centre + offset
    assert CTF.rotation_origin((-31, -61, -10, -20), 0, 0, 0) == (-20, -40)  # toward zero


def test_rotation_polygons_are_fun_004e6330():
    from ctgedit.format import ctf as CTF

    box = (10, 20, 30, 40)
    # a quarter turn clockwise (y down) about the box's top-left corner: exact
    polys = CTF.rotation_polygons(box, (10, 20), 90, 90)
    assert polys == [[(10, 20), (10, 40), (-10, 40), (-10, 20)]]
    # 0 degrees = the box itself; 180 about the centre maps it onto itself
    assert CTF.rotation_polygons(box, (20, 30), 0, 0) == [[(10, 20), (30, 20), (30, 40), (10, 40)]]
    assert sorted(CTF.rotation_polygons(box, (20, 30), 180, 180)[0]) == sorted(
        [(10, 20), (30, 20), (30, 40), (10, 40)]
    )
    assert len(CTF.rotation_polygons(box, (0, 0), -45, 45)) == 91


class _Rot:
    """What ctf._rot_sweep reads of a record."""

    def __init__(self, bounds, **keys):
        self.bounds = bounds
        self._k = {k: str(v) for k, v in keys.items()}

    def get(self, key):
        return self._k.get(key)


def test_rot_sweep_negates_the_minimum_and_uses_the_express_edge():
    from ctgedit.format import ctf as CTF

    line = (100, 100, 101, 200)  # a 1 px wide, 100 px tall line
    # about its BOTTOM end (express ROTYORG 1) over a full turn: a disc of
    # radius 100 around (100, 200)
    box = CTF._rot_sweep(_Rot(line, ROT="A", ROTYORG=1, ROTMINPC=0, ROTMAXPC=360), line)
    assert box[0] <= 1 and box[2] >= 199 and box[1] <= 101 and box[3] >= 299
    assert box[0] >= -1 and box[3] <= 301
    # at minimum the angle is ANTI-clockwise: ROTMINPC 90 / ROTMAXPC 0 sweeps
    # -90..0 about the bottom end = the quarter to the LEFT of the line
    box = CTF._rot_sweep(_Rot(line, ROT="A", ROTYORG=1, ROTMINPC=90, ROTMAXPC=0), line)
    assert box[0] <= 1 and box[2] <= 102 and box[3] <= 201
    # a custom point is an offset from the centre, not a page position
    box = CTF._rot_sweep(
        _Rot(line, ROT="A", ROTMODE=1, ROTXORG=0, ROTYORG=50, ROTMINPC=0, ROTMAXPC=180), line
    )
    assert box[1] >= 99 and box[3] <= 301


@pytest.mark.golden
def test_ctf_of_an_off_centre_rotator_is_byte_exact(MOTION):
    """FxMotion: three gauge needles (tc 0x01) rotating 0..360 about their BOTTOM end
    (express ROTYORG 1) -- the .ctF CtDraw saved with the page (its PageOpen + PageSave
    of FxMotion) is rebuilt byte for byte (but the save stamps, and the deflate of the
    DIBs without the classic zlib: tests/ctfcompare.py)."""
    import ctfcompare
    import fxinstall
    from ctgedit.format import ctf as CTF
    from ctgedit.format import genie_place as GP
    from ctgedit.format.container import Container
    from ctgedit.format.page import parse_page

    a = fxinstall.oracle_bytes("pages", "FxMain/FxMotion", "page.ctg")
    b = fxinstall.oracle_bytes("pages", "FxMain/FxMotion", "page.ctF")
    ca = Container.parse(a)
    # saved together: the same page version
    assert ctfcompare.page_identity(a)[4:] == ctfcompare.page_identity(b)[4:]
    rot = []
    for po in parse_page(ca.block("page"), ca.word2).objects:
        r = GP.decode(po)
        bag = r.entries[0][1] if r.entries and len(r.entries[0]) > 1 else {}
        if po.type_code == 0x01 and bag.get("ROTYORG") == "1" and not bag.get("ROTMODE"):
            rot.append(po)
    assert len(rot) >= 3
    ctfcompare.assert_same_ctf(CTF.build_ctf(a, MOTION), b, stamps=True)


def test_off_centre_sweep_decides_what_the_ctf_bakes(MOTION, tmp_path):
    """The dynamic region of a needle turning about its bottom end reaches a
    static rectangle beside that end (CtDraw keeps it as a record); one
    beyond the needle's reach is baked into the background."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import ctf as CTF

    doc = EditableDocument.open(_copy(tmp_path, MOTION))
    # far off the page: no other object's sweep (FxSzyMove's table scroll
    # moves 4000 px) reaches here
    needle = doc.create_object("line", rect=(12000, 12000, 12000, 12100), colour=0x000000)
    for k, v in (("ROT", "Angle_1"), ("ROTMINPC", "0"), ("ROTMAXPC", "360"), ("ROTYORG", "1")):
        assert doc.ensure_property(needle, k, v)
    near = _rect(doc, (11920, 12095, 11930, 12105))  # 70-80 px left of the bottom end
    far = _rect(doc, (11800, 12095, 11810, 12105))  # 190 px away: out of reach
    baked, _keep, _par, _drawn = CTF.bake_plan(doc.parsed.page.objects)
    assert needle not in baked
    assert near not in baked and far in baked
    # (turning about the CENTRE the sweep would not reach it: without the rotation it is
    # baked too)
    assert doc.remove_property(needle, "ROT")
    baked, _keep, _par, _drawn = CTF.bake_plan(doc.parsed.page.objects)
    assert near in baked
