"""Access, Metadata and Alarm Indicator pages, and object names."""

import os
import struct

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402

from ctgedit.format import alarmind as AI  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402

import fxinstall  # noqa: E402
from qtkit import app as _app

#: the page of the object tests (its name is the ActiveX EVENTCLASS placeholder's
#: '%0.8s' and the pg rows' PAGE)
PAGE = "FxGenies"
#: the placed genie of PAGE the genie tests use
GENIE = "FxPump1"


@pytest.fixture
def page(fx_page):
    """FxMain's FxGenies in the fx_install copy (a copy: a test may write it)."""
    return fx_page(PAGE)


def _doc(path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(str(path))
    doc.derive_check = True  # every derive checked against a full one
    return doc


def _genie(doc):
    """(root, member): FxPump1 and its first member (a rectangle)."""
    (g,) = doc.find_objects_by_name(GENIE)
    assert doc.page.objects[g].is_genie
    return g, doc.page.objects[g].members[0]


def _oracle_page(fx_install, step, unit, name):
    """The page CtDraw saved in oracle unit ``step``/``unit``, written beside FxMain's
    pages in the fx_install copy as ``name``.ctg (so its libraries resolve)."""
    data = fxinstall.oracle_bytes(step, unit, "page.ctg")
    path = os.path.join(fx_install.project("FxMain"), name + ".ctg")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _sheet(doc, index, **kw):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index, **kw)


def _rect2(doc, i):
    return tuple(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect2)


def _section(po):
    r = GP.decode(po)
    return r.cls[: GP.metadata_len(r.cls)] if po.subtype_size & 0x100 else b""


# ------------------------------------------------------------------ object names
def test_name_rule_is_ctdraws():
    """not a digit, '_' or punctuation first; no punctuation or
    spaces anywhere; 32 characters (the field's limit)."""
    from ctgedit.editing import object_name_rule_problem as P, unique_object_name as U

    for ok in ("Pump1", "AlarmList", "Tab_Bar", "x", "Pump_1_2", ""):
        assert P(ok) == "", ok
    for bad in (
        "1Pump",
        "_Pump",
        "Pump 1",
        "Pump.1",
        "Pump-1",
        "Pump(1)",
        "%Tag%",
        "a" * 33,
        "Pump#",
        "Pump,1",
        "Pu=mp",
    ):
        assert P(bad), bad
    taken = {"pump", "pump_1", "tank_3", "valve_x"}
    t = lambda c: c.lower() in taken  # noqa: E731
    assert U("Pump", t) == "Pump_2"  # Pump -> Pump_1 (taken) -> Pump_2
    assert U("Tank_3", t) == "Tank_4"
    assert U("Valve_x", t) == "Valve_x_1"  # a non-number after '_' appends '_1'
    assert U("Free", t) == "Free"
    assert U("a" * 31, lambda c: True) == ""  # past 32 characters: dropped


def test_names_are_unique_in_their_namespace(page):
    doc = _doc(page)
    a = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    b = doc.create_object("rectangle", rect=(60, 5, 100, 40), colour=0x336699)
    assert doc.set_object_name(a, "Pump1")
    po = doc.parsed.page.objects[a]
    assert G.parse_geom_header(po.body_raw).name == "Pump1"
    assert doc.object_properties(a)["__AN_NAME__"] == "Pump1"
    # CtDraw compares without case: refused, nothing written
    n = len(doc._undo)
    assert "already used" in doc.object_name_problem(b, "PUMP1")
    with pytest.raises(ValueError):
        doc.set_object_name(b, "pump1")
    assert len(doc._undo) == n and "__AN_NAME__" not in doc.object_properties(b)
    with pytest.raises(ValueError):
        doc.set_object_name(b, "1st")  # the character rule
    # a group is its own namespace: the same name is fine inside it, the full
    # name is Group.Name
    c = doc.create_object("rectangle", rect=(120, 5, 160, 40), colour=0x336699)
    d = doc.create_object("rectangle", rect=(170, 5, 210, 40), colour=0x336699)
    g = doc.group_objects([c, d])
    kids = [j for j in doc.subtree(g) if j != g]
    assert doc.object_name_problem(kids[0], "Pump1") == ""
    assert doc.set_object_name(kids[0], "Pump1")
    assert "already used" in doc.object_name_problem(kids[1], "pump1")
    assert doc.set_object_name(g, "Skid")
    assert doc.qualified_name(kids[0]) == "Skid.Pump1"
    # Goto by name: the plain name finds both, the full name one
    assert doc.find_objects_by_name("Pump1") == sorted([a, kids[0]])
    assert doc.find_objects_by_name("skid.pump1") == [kids[0]]
    assert doc.find_objects_by_name("Other.Pump1") == []


def test_access_sheet_refuses_a_duplicate_name(page):
    doc = _doc(page)
    a = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    b = doc.create_object("rectangle", rect=(60, 5, 100, 40), colour=0x336699)
    assert doc.set_object_name(a, "Feeder")
    dlg = _sheet(doc, b)
    ed = dlg.access_editor
    ed.name.setText("feeder")
    ed.desc.setText("the second feeder")
    n = len(doc._undo)
    assert dlg.apply() is False and len(doc._undo) == n
    assert "already used" in dlg._status.text()
    assert "DESC" not in doc.object_properties(b)  # nothing of the sheet is written
    ed.name.setText("Feeder2")
    assert dlg.apply() and dlg._status.isHidden() and len(doc._undo) == n + 1
    rp = doc.object_properties(b)
    assert rp["__AN_NAME__"] == "Feeder2" and rp["DESC"] == "the second feeder"
    assert G.parse_geom_header(doc.parsed.page.objects[b].body_raw).name == "Feeder2"
    ed.name.setText("2nd")
    assert dlg.apply() is False and "must begin with a letter" in dlg._status.text()


def test_duplicate_and_paste_rename_like_ctdraw(page):
    doc = _doc(page)
    a = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    assert doc.set_object_name(a, "Motor")
    d1 = doc.duplicate_objects([a])[0]
    d2 = doc.duplicate_objects([a])[0]
    assert (doc.object_name(d1), doc.object_name(d2)) == ("Motor_1", "Motor_2")
    assert G.parse_geom_header(doc.parsed.page.objects[d2].body_raw).name == "Motor_2"
    new = doc.paste_payload(doc.copy_payload([d1]))
    assert doc.object_name(new[0]) == "Motor_3"
    # a copy of a group keeps its members' names (their namespace is the copy)
    c = doc.create_object("rectangle", rect=(120, 5, 160, 40), colour=0x336699)
    e = doc.create_object("rectangle", rect=(170, 5, 210, 40), colour=0x336699)
    g = doc.group_objects([c, e])
    member = [j for j in doc.subtree(g) if j != g][0]
    assert doc.set_object_name(member, "Motor")
    assert doc.set_object_name(g, "Skid")
    dg = doc.duplicate_objects([g])[0]
    assert doc.object_name(dg) == "Skid_1"
    assert doc.object_name([j for j in doc.subtree(dg) if j != dg][0]) == "Motor"
    # a placed GENIE root is renamed too, in its value AND template bag --
    # live: CtDraw's copy + paste of a named ExampleSA genie made '<name>_1'
    # in both and in the header
    root, _member = _genie(doc)
    assert doc.set_object_name(root, "Feeder")
    copy = doc.duplicate_objects([root])[0]
    po = doc.parsed.page.objects[copy]
    assert po.subtype_size & 0x200
    assert doc.object_name(copy) == "Feeder_1"
    from ctgedit.format import genie_place as GP

    e0 = GP.decode(po).entries[0]
    assert e0[1].get("__AN_NAME__") == "Feeder_1"
    if e0[2].get("__AN_NAME__") is not None:
        assert e0[2].get("__AN_NAME__") == "Feeder_1"
    assert G.parse_geom_header(po.body_raw).name == "Feeder_1"
    assert doc.object_name(root) == "Feeder"


@pytest.mark.golden
def test_paste_names_are_ctdraws(fx_install):
    """CtDraw's ClipboardCopy + ClipboardPaste of the named group FxManyGroup1 and the
    named genie FxManyPump1 (oracle draw/copy_paste_named): the copies take the names our
    duplicate gives (unique_object_name over the page's top-level names)."""
    from ctgedit.editing import EditableDocument

    path = _oracle_page(fx_install, "draw", "copy_paste_named", "FxManyPasted")
    ours = EditableDocument.open(fx_install.page("FxMany"))
    theirs = EditableDocument.open(path)
    for name in ("FxManyGroup1", "FxManyPump1"):
        (i,) = ours.find_objects_by_name(name)
        copy = ours.duplicate_objects([i])[0]
        want = ours.object_name(copy)
        assert want != name
        assert len(theirs.find_objects_by_name(want)) == 1, (name, want)
        assert len(theirs.find_objects_by_name(name)) == 1, name


def test_legacy_name_objects_keep_their_form(fx_page):
    """The SA help navigation links are named by the legacy NAME key (== the
    header name; CtDraw's own paste made 'Link_1'): a rename updates NAME and
    the header, adds no __AN_NAME__, and the namespace is the group."""
    try:
        path = fx_page("FxLegacyNames")
    except KeyError:
        pytest.skip(
            "needs fixture: FxMain/FxLegacyNames, a page with legacy NAME-keyed "
            "groups (the bag key NAME == the header name, no __AN_NAME__): a "
            "top-level group 'FxLink' holding 'FxShadow' and an unnamed sibling, and "
            "a top-level group 'FxLink_1' with its own member 'FxShadow'"
        )
    doc = _doc(path)
    objs = doc.parsed.page.objects
    (link,) = doc.find_objects_by_name("FxLink")
    (link1,) = doc.find_objects_by_name("FxLink_1")
    (shadow,) = doc.find_objects_by_name("FxLink.FxShadow")
    sibling = next(m for m in doc.page.objects[link].members if m != shadow)
    other = next(m for m in doc.page.objects[link1].members)
    assert doc.object_name(link) == "FxLink" and doc.object_name(link1) == "FxLink_1"
    assert doc.object_name(shadow) == "FxShadow" and doc.page.objects[shadow].parent == link
    fl = objs[shadow].subtype_size
    assert doc.set_object_name(shadow, "Shade")
    rp = doc.object_properties(shadow)
    assert rp["NAME"] == "Shade" and "__AN_NAME__" not in rp
    assert G.parse_geom_header(objs[shadow].body_raw).name == "Shade"
    assert objs[shadow].subtype_size == fl
    assert doc.qualified_name(shadow) == "FxLink.Shade"
    assert "already used" in doc.object_name_problem(sibling, "SHADE")  # same group
    assert doc.object_name_problem(other, "Shade") == ""  # FxLink_1's own namespace
    assert doc.find_objects_by_name("fxlink.shade") == [shadow]
    # a copy of a named top-level group gets CtDraw's next free name
    taken = {doc.object_name(r).lower() for r in doc.page.roots()}
    new = doc.duplicate_objects([link])[0]
    from ctgedit.editing import unique_object_name

    assert doc.object_name(new) == unique_object_name("FxLink", lambda c: c.lower() in taken)
    assert doc.object_properties(new)["NAME"] == doc.object_name(new)


# ------------------------------------------------------------------ Access > General
def test_same_area_and_no_privilege_boxes(page):
    """Ticked = no AREA / PRIV key (the field shows the page's area or
    '<All areas>' / '<None>'); unticked = the typed value; the menus list
    CtDraw's 1..255 and 1..8."""
    doc = _doc(page)
    r = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    assert doc.ensure_property(r, "PRIV", "3")
    dlg = _sheet(doc, r)
    ed = dlg.access_editor
    assert ed.same_area.isChecked() and not ed.area.isEnabled()
    from ctgedit.io.project import page_area

    assert ed.area.placeholderText() == (page_area(doc.path) or "<All areas>")
    assert not ed.no_priv.isChecked() and ed.priv.text() == "3" and ed.priv.isEnabled()
    ed.same_area.setChecked(False)
    assert ed.area.isEnabled() and ed.area_btn.isEnabled()
    ed._fill_areas(ed.area_btn.menu())
    subs = [a.menu() for a in ed.area_btn.menu().actions()]
    assert sum(len(m.actions()) for m in subs) == 255
    subs[0].actions()[4].trigger()  # area 5
    assert ed.area.text() == "5"
    ed.no_priv.setChecked(True)
    assert ed.priv.text() == "" and ed.priv.placeholderText() == "<None>"
    assert dlg.apply()
    rp = doc.object_properties(r)
    assert rp["AREA"] == "5" and "PRIV" not in rp
    ed._fill_privileges(ed.priv_btn.menu())
    assert [a.text() for a in ed.priv_btn.menu().actions()] == [str(n) for n in range(1, 9)]
    ed.no_priv.setChecked(False)
    assert ed.priv.text() == "3"  # the value before it was ticked
    ed.same_area.setChecked(True)
    assert dlg.apply()
    rp = doc.object_properties(r)
    assert "AREA" not in rp and rp["PRIV"] == "3"
    # Clear Property: every Access > General setting goes, the boxes follow
    ed.desc.setText("feeder pump")
    ed.same_area.setChecked(False)
    ed.area.setText("7")
    assert dlg.apply()
    page = dlg.page_widget("access")
    clear = [b for b in page.findChildren(QtWidgets.QPushButton) if b.text() == "Clear Property"]
    assert len(clear) == 1
    clear[0].click()
    assert ed.same_area.isChecked() and ed.no_priv.isChecked() and not ed.area.isEnabled()
    assert dlg.apply()
    rp = doc.object_properties(r)
    assert not {"DESC", "AREA", "PRIV"} & set(rp)


def test_log_device_menu_and_advice(page, fx_project):
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import device_logs_messages, devices

    # the page in its project (the devices of its include chain); never saved
    doc = EditableDocument.open(page)
    devs = devices(fx_project("FxMain"))
    assert [d["NAME"] for d in devs] == ["FxLog", "FxPrinter"]
    r = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    dlg = _sheet(doc, r)
    ed = dlg.access_editor
    ed._fill_devices(ed.device_btn.menu())
    names = [a.text().split("  (")[0] for a in ed.device_btn.menu().actions()]
    assert names == [d["NAME"] for d in devs]
    ed.device.setText("NoSuchDevice")
    assert not ed.device_hint.isHidden() and "No device named" in ed.device_hint.text()
    good = [d for d in devs if device_logs_messages(d)]
    assert good
    ed.device.setText(good[0]["NAME"])
    assert ed.device_hint.isHidden()
    plain = [d for d in devs if not device_logs_messages(d)]
    assert plain
    ed.device.setText(plain[0]["NAME"])
    assert "{MsgLog}" in ed.device_hint.text()


def test_genie_member_keeps_access_general_editable(page):
    """Live CtDraw 8.50 (Ctrl+double-click on a member): every page greyed
    but Access > General; Description, Tool tip, area, privilege and Log
    device stay editable (stored 'value<User>'), the name greyed."""
    doc = _doc(page)
    root, m = _genie(doc)
    assert doc.page.objects[m].parent == root and doc.page.objects[root].is_genie
    dlg = _sheet(doc, m)
    assert dlg.is_read_only() and dlg.is_partly_editable()
    box = dlg._buttons
    assert box.button(QtWidgets.QDialogButtonBox.Apply) is not None
    ed = dlg.access_editor
    assert ed.name.isReadOnly()
    for w in (ed.desc, ed.tip, ed.device):
        assert not w.isReadOnly() and w.isEnabled()
    assert ed.same_area.isEnabled() and ed.no_priv.isEnabled()
    vis = dlg._editors["VISIBLE"]
    assert vis.isReadOnly()
    assert dlg.metadata_editor._ro
    before = dict(doc.object_properties(m))
    n = len(doc._undo)
    ed.desc.setText("the motor")
    ed.same_area.setChecked(False)
    ed.area.setText("4")
    vis.set_value("X = 1")  # read-only: never written
    assert dlg.apply() and len(doc._undo) == n + 1
    rp = doc.object_properties(m)
    assert rp["DESC"] == "the motor<User>" and rp["AREA"] == "4<User>"
    assert rp.get("VISIBLE") == before.get("VISIBLE")
    # shown without the marker, kept on the next write
    dlg = _sheet(doc, m)
    assert dlg.access_editor.desc.text() == "the motor"
    assert dlg.access_editor.area.text() == "4"
    dlg.access_editor.tip.setText("tip")
    assert dlg.apply()
    rp = doc.object_properties(m)
    assert rp["DESC"] == "the motor<User>" and rp["COMMENT"] == "tip<User>"
    doc.undo()
    doc.undo()
    assert doc.object_properties(m) == before


def test_window_member_sheet_edits_access_general(page, monkeypatch):
    """The window opens a genie member's sheet with its own read-only verdict:
    Access > General stays editable there too, one Ctrl+Z."""
    from ctgedit.ui.main_window import MainWindow

    # closing the window over the unsaved edit: discard it
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Discard)
    )
    _app()
    win = MainWindow()
    try:
        win.open_document(page)
        _root, m = _genie(win.doc)
        win.object_properties(m)
        dlg = win._dialogs[("objprops", m)]
        assert dlg.is_read_only() and dlg.is_partly_editable()
        dlg.access_editor.tip.setText("screw motor")
        box = dlg._buttons
        box.button(QtWidgets.QDialogButtonBox.Apply).click()
        assert win.doc.object_properties(m)["COMMENT"] == "screw motor<User>"
        assert win.act_undo.text() == "Undo Object Properties"
        dlg.reject()
    finally:
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.close()


def test_genie_root_sheet(page):
    """A placed genie: Access / Metadata / Alarm Indicator; only the name of
    Access > General and the alarm indicator can be set (the page load drops
    the rest -- metadata included)."""
    doc = _doc(page)
    root, _m = _genie(doc)
    dlg = _sheet(doc, root)
    assert [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())] == [
        "Access",
        "Metadata",
        "Alarm Indicator",
    ]
    ed = dlg.access_editor
    assert ed.name.isEnabled()
    for w in (ed.desc, ed.tip, ed.area, ed.priv, ed.device):
        assert not w.isEnabled()
    assert not ed.same_area.isEnabled() and not ed.area_btn.isEnabled()
    assert dlg.metadata_editor._ro and not dlg.metadata_editor.btn_add.isEnabled()
    assert not dlg.alarm_editor._ro and dlg.alarm_editor.has.isEnabled()
    assert doc.set_metadata(root, [("Equipment", "X")]) is False
    assert "METADATA_0" not in doc.object_properties(root)


# ------------------------------------------------------------------ Access > Disable
def test_disable_style_is_four_radios(page):
    from ctgedit.ui.objprops.fields import _RadioChoice

    doc = _doc(page)
    r = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    dlg = _sheet(doc, r)
    w = dlg._widgets["GREY_STYLE"][0][1]
    assert isinstance(w, _RadioChoice)
    assert [b.text() for b in w.buttons()] == ["Embossed", "Greyed", "Hidden", "Unchanged"]
    # an absent style shows CtDraw's default (Embossed) checked, nothing stored
    assert w.buttons()[0].isChecked() and w.currentData() is None
    w.buttons()[3].click()
    assert w.currentData() == "GRAY_UNCHANGED"
    assert dlg.apply() and doc.object_properties(r)["GREY_STYLE"] == "GRAY_UNCHANGED"
    dlg = _sheet(doc, r)
    w = dlg._widgets["GREY_STYLE"][0][1]
    assert w.buttons()[3].isChecked() and w.currentText() == "Unchanged"
    w.buttons()[0].click()  # Embossed: now a real choice
    assert dlg.apply() and doc.object_properties(r)["GREY_STYLE"] == "GRAY_SUNK"


def test_disable_style_falls_back_as_ctdraws_page(page):
    """CtDraw's Disable page shows GRAY_ALL as Greyed and any value it does not
    name as Embossed; its apply stores the radio it shows."""
    doc = _doc(page)
    r = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    for stored, shown, written in (
        ("GRAY_ALL", "GRAY_PART", "GRAY_PART"),
        ("SOMETHING", "GRAY_SUNK", "GRAY_SUNK"),
    ):
        assert doc.ensure_property(r, "DISABLED", "Tag1")
        assert doc.ensure_property(r, "GREY_STYLE", stored)
        dlg = _sheet(doc, r)
        w = dlg._widgets["GREY_STYLE"][0][1]
        assert w.currentData() == shown
        e = dlg._widgets["DISABLED"][0][1]
        e.setText("Tag2")
        assert dlg.apply()
        assert doc.object_properties(r)["GREY_STYLE"] == written


# ------------------------------------------------------------------ Identification
def test_activex_identification_page(page):
    doc = _doc(page)
    x = doc.create_object("activex", rect=(300, 300, 400, 380))
    dlg = _sheet(doc, x)
    assert "identification" in dlg.page_ids()
    name = dlg._widgets["ACTIVEXEXT_*NAME"][0][1]
    evc = dlg._widgets["ACTIVEXEXT_*EVENTCLASS"][0][1]
    persist = dlg._widgets["ACTIVEXEXT_*PERSIST"][0][1]
    an = doc.page.objects[x].an
    assert name.placeholderText() == "AN%d" % an
    assert evc.placeholderText() == "%s_AN%d" % (PAGE[:8], an)  # '%0.8s_AN%s'
    assert name.maxLength() == 64 and evc.maxLength() == 16
    name.setText("Meter1")
    evc.setText("Meter1_E")
    persist.setChecked(True)
    assert dlg.apply()
    rp = doc.object_properties(x)
    assert (rp["ACTIVEXEXT_*NAME"], rp["ACTIVEXEXT_*EVENTCLASS"], rp["ACTIVEXEXT_*PERSIST"]) == (
        "Meter1",
        "Meter1_E",
        "TRUE",
    )
    from ctgedit.io import pgregister

    rows = pgregister.activex_rows(doc.serialize(), PAGE)
    assert any(rw.get("OBJ_NAME") == "Meter1" for rw in rows)
    persist.setChecked(False)
    assert dlg.apply() and doc.object_properties(x)["ACTIVEXEXT_*PERSIST"] == "FALSE"
    page = dlg.page_widget("identification")
    clear = [b for b in page.findChildren(QtWidgets.QPushButton) if b.text() == "Clear Property"]
    clear[0].click()  # back to the defaults
    assert dlg.apply()
    rp = doc.object_properties(x)
    assert "ACTIVEXEXT_*NAME" not in rp and "ACTIVEXEXT_*EVENTCLASS" not in rp
    assert rp["ACTIVEXEXT_*PERSIST"] == "FALSE"


# ------------------------------------------------------------------ Metadata
def test_metadata_rules():
    from ctgedit.editing import metadata_problem as P

    assert P([("Equipment", "Pump1"), ("_x", ""), ("2nd", "'a b'"), ("%Tag%Name", "#5")]) is None
    assert P([("Equipment", "a"), ("EQUIPMENT", "b")])[0] == 1  # unique, no case
    assert P([("Bad name", "x")]) and P([("A.B", "x")]) and P([("AND", "x")])
    assert P([("", "x")]) and P([("x" * 254, "")]) and P([("A", "v" * 256)])
    assert P([("%Tag", "x")])  # an open '%'


def _metadata_records_pass_the_rules(path):
    from ctgedit.editing import EditableDocument, metadata_problem

    doc = EditableDocument.open(path)
    n = 0
    for i, po in enumerate(doc.parsed.page.objects):
        if po.subtype_size & 0x100:
            items = doc.metadata(i)
            assert metadata_problem(items) is None, (i, items)
            # and the stored section is what the rebuild writes
            assert _section(po) == GP.metadata_section(GP.decode(po).entries[0][1])
            n += 1
    return n


def test_every_fixture_metadata_passes_the_rules(fx_page):
    """FxMain's FxMany: metadata on 22 records (12 styled shapes, 10 texts)."""
    assert _metadata_records_pass_the_rules(fx_page("FxMany")) > 20


@pytest.mark.golden
def test_every_ctdraw_saved_metadata_passes_the_rules(fx_install):
    """The same records as CtDraw's load + save wrote them (oracle pages/FxMain/FxMany)."""
    path = _oracle_page(fx_install, "pages", "FxMain/FxMany", "FxManyCtDraw")
    assert _metadata_records_pass_the_rules(path) > 20


def test_metadata_page_writes_bag_section_and_ctf(page):
    """A METADATA edit changes the bag AND the dwFlags-0x100 section, and
    the runtime's .ctF (which keeps the section, not the bags) carries it."""
    from ctgedit.format import ctf as CTF
    from ctgedit.format.container import Container
    from ctgedit.format.page import parse_page

    doc = _doc(page)
    r = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    assert not doc.parsed.page.objects[r].subtype_size & 0x100
    block = G.find_colour_block_exact(doc.parsed.page.objects[r])
    assert block is not None
    dlg = _sheet(doc, r)
    md = dlg.metadata_editor
    md.add_row()
    md.table.item(0, 0).setText("Equipment")
    md.table.item(0, 1).setText("Pump1")
    md.add_row()
    md.table.item(1, 0).setText("Mode")
    md.table.item(1, 1).setText("2")
    md.add_row()  # an empty row is not stored
    assert dlg.tab_marked("Metadata")  # marked as it is typed
    n = len(doc._undo)
    assert dlg.apply() and len(doc._undo) == n + 1
    po = doc.parsed.page.objects[r]
    rp = doc.object_properties(r)
    assert (rp["METADATA_0"], rp["METADATA_1"]) == ("Equipment=Pump1", "Mode=2")
    assert po.subtype_size & 0x100 and po.subtype_size & 0x10
    want = struct.pack("<H", 2) + b"".join(
        struct.pack("<H", len(s)) + s for s in (b"Equipment", b"*Pump1", b"Mode", b"*2")
    )
    assert _section(po) == want
    assert G.metadata_span(po) is not None
    # the section sits between the style block and the class tail: the style
    # (the object's colour) is still found and drawn
    cb = G.find_colour_block_exact(po)
    assert cb is not None and (cb.flags, cb.slots) == (block.flags, block.slots)
    assert doc.page.objects[r].style.fill_colour == 0x336699
    # the saved page reads back the same
    data = doc.serialize()
    from ctgedit.format.page import parse_ctg_bytes

    back = parse_ctg_bytes(data).page.objects[r]
    assert _section(back) == want and back.body_raw == po.body_raw
    # the .ctF -- as the save builds it, and with every record kept -- carries it
    h = G.get_handle(po)
    for bake in (True, False):
        out = Container.parse(CTF.build_ctf(data, doc.path, bake=bake))
        rec = next(
            o
            for o in parse_page(out.block("page"), out.word2).objects
            if o.type_code == po.type_code and G.get_handle(o) == h
        )
        assert rec.subtype_size & 0x100 and _section(rec) == want, bake
    # an edit rewrites it; removing every row clears the section and the bit
    dlg = _sheet(doc, r)
    md = dlg.metadata_editor
    md.table.item(0, 1).setText("Pump2")
    md.table.selectRow(1)
    md.delete_rows()
    assert dlg.apply()
    po = doc.parsed.page.objects[r]
    assert doc.metadata(r) == [("Equipment", "Pump2")]
    assert (
        _section(po)
        == struct.pack("<H", 1) + struct.pack("<H", 9) + b"Equipment" + struct.pack("<H", 6) + b"*Pump2"
    )
    assert doc.set_metadata(r, []) and not doc.parsed.page.objects[r].subtype_size & 0x100
    assert _section(doc.parsed.page.objects[r]) == b""
    doc.undo()
    assert doc.metadata(r) == [("Equipment", "Pump2")]
    assert doc.parsed.page.objects[r].subtype_size & 0x100


def test_metadata_page_refuses_bad_rows(page):
    doc = _doc(page)
    r = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    dlg = _sheet(doc, r)
    md = dlg.metadata_editor
    for name in ("Equipment", "equipment"):
        md.add_row()
        md.table.item(md.table.rowCount() - 1, 0).setText(name)
    n = len(doc._undo)
    assert dlg.apply() is False and len(doc._undo) == n
    assert "used twice" in dlg._status.text()
    md.table.item(1, 0).setText("Bad name")
    assert dlg.apply() is False and "not a valid metadata name" in dlg._status.text()
    md.table.item(1, 0).setText("Other")
    assert dlg.apply() and doc.metadata(r) == [("Equipment", ""), ("Other", "")]


def test_metadata_property_edit_rebuilds_the_section(page):
    """Any METADATA_ bag edit (the Properties panel, scripts) re-derives the
    section (editing._after_bag_edit)."""
    doc = _doc(page)
    r = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    assert doc.ensure_property(r, "METADATA_0", "Tag=T1")
    po = doc.parsed.page.objects[r]
    assert po.subtype_size & 0x100 and _section(po).endswith(b"*T1")
    assert doc.set_property(r, "METADATA_0", "Tag=T2")
    assert _section(doc.parsed.page.objects[r]).endswith(b"*T2")
    assert doc.remove_property(r, "METADATA_0")
    assert not doc.parsed.page.objects[r].subtype_size & 0x100
    # the saver's rule: the bit follows ANY
    # METADATA_ key, the section runs METADATA_0.. up to the first gap
    assert doc.ensure_property(r, "METADATA_1", "Mode=2")
    po = doc.parsed.page.objects[r]
    assert po.subtype_size & 0x100 and _section(po) == b"\x00\x00"
    # the Metadata page lists every METADATA_ key -- an Apply
    # behind a gap keeps the row, renumbered from 0
    assert doc.metadata(r) == [("Mode", "2")]
    assert doc.set_metadata(r, [("Mode", "2"), ("Area", "4")])
    rp = doc.object_properties(r)
    assert (rp["METADATA_0"], rp["METADATA_1"]) == ("Mode=2", "Area=4")
    assert _section(doc.parsed.page.objects[r]).endswith(b"*4")


def test_metadata_section_rule():
    """over the value bag: stop at a missing or EMPTY value,
    split like strtok_s (leading '=' skipped, the value keeps later '=')."""
    bag = GP.Bag(
        [("METADATA_0", "a=b=c"), ("METADATA_1", "=x=y"), ("METADATA_2", ""), ("METADATA_3", "late=1")]
    )
    sec = GP.metadata_section(bag)
    assert sec == struct.pack("<H", 2) + b"".join(
        struct.pack("<H", len(s)) + s for s in (b"a", b"*b=c", b"x", b"*y")
    )
    assert GP.has_metadata(bag) and GP.has_metadata(GP.Bag([("METADATA_7", "")]))
    assert not GP.has_metadata(GP.Bag([("METADATA_", "x"), ("Metadata_0", "x")]))


def _styled_metadata_keeps_its_style(many, kinds):
    """``many`` = a FxMany page, ``kinds`` = a FxTextKinds page (the fixture's or the
    CtDraw-saved one)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import colourstate as CS
    from ctgedit.io import reader

    doc = EditableDocument.open(many)
    objs = doc.parsed.page.objects
    styled = [i for i, po in enumerate(objs) if po.subtype_size & 0x102 == 0x102]
    assert len(styled) >= 20
    for i in styled:
        po = objs[i]
        cb = G.find_colour_block_exact(po)
        assert cb is not None and cb.offset == CS.layout(po)["style_at"], i
        s, e = G.metadata_span(po)
        assert po.body_raw[s:e] == GP.metadata_section(GP.decode(po).entries[0][1]), i
    doc = EditableDocument.open(kinds)
    (i,) = doc.find_objects_by_name("FxBold")
    o = reader.read_page(kinds).objects[i]
    assert o.style.text == "bold" and o.style.bold  # attribs 0x10
    # the text tail's block offset is the block's too (before the metadata)
    po = doc.parsed.page.objects[i]
    assert po.subtype_size & 0x100
    assert (
        G.parse_text_tail(po)["block_off"]
        == G.find_colour_block_exact(po).offset
        == CS.layout(po)["style_at"]
    )


def test_styled_records_with_metadata_keep_their_style(fx_page):
    """Styled records with metadata keep their style block."""
    _styled_metadata_keeps_its_style(fx_page("FxMany"), fx_page("FxTextKinds"))


@pytest.mark.golden
def test_ctdraw_saved_styled_records_with_metadata_keep_their_style(fx_install):
    """The same records as CtDraw's load + save wrote them."""
    many = _oracle_page(fx_install, "pages", "FxMain/FxMany", "FxManyCtDraw")
    kinds = _oracle_page(fx_install, "pages", "FxMain/FxTextKinds", "FxTextKindsCtDraw")
    _styled_metadata_keeps_its_style(many, kinds)


# ------------------------------------------------------------------ Alarm Indicator
def test_indicator_geometry_and_bounds_rule():
    """for width 3 / padding 2:
    inner = U + 1 + pad, outer = inner + W + 1, the Bounds grown by W + 2 +
    pad and united with the flag at its place; inside: no growth."""
    U = (100, 50, 200, 90)
    s = AI.settings({"AI_ON": "1", "AI_WIDTH": "3", "AI_PADDING": "2"})
    outer, inner, strip = AI.frame(U, s)
    assert inner == (97, 47, 203, 93) and outer == (93, 43, 207, 97) and strip == 4
    assert AI.flag_xy(0, outer, 3, 20, 20) == (75, 48)  # top left, 20 x 20 default
    assert AI.grown_bounds(U, s) == (75, 43, 207, 97)
    s = AI.settings({"AI_ON": "1", "AI_WIDTH": "3", "AI_PADDING": "2", "AI_FLAG_ON": "0"})
    assert AI.grown_bounds(U, s) == (93, 43, 207, 97)
    s = AI.settings({"AI_ON": "1", "AI_INSIDE": "1", "AI_WIDTH": "15", "AI_FLAG_ON": "0"})
    assert AI.frame(U, s)[0] == U and AI.grown_bounds(U, s) == U
    assert AI.grown_bounds(U, AI.settings({"AI_ON": "0"})) == U
    # live CtDraw 8.50: AI_ON 1 with the defaults on a genie
    # root grew its Bounds [830,300,903,335] to [802,290,913,345]
    assert AI.grown_bounds((830, 300, 903, 335), AI.settings({"AI_ON": "1"})) == (802, 290, 913, 345)
    # the page's defaults and CtDraw's full apply
    v = AI.page_values({})
    assert (v["eqhier"], v["width"], v["padding"], v["colour"], v["flag_on"]) == (1, 3, 5, 0xBFBFBF, True)
    v.update(has=True, inside=True)
    keys = [k for k, _v in AI.apply_edits(v)]
    assert "AI_PADDING" not in keys and ("AI_EXPR", None) in AI.apply_edits(v)
    assert dict(AI.apply_edits(v))["AI_COLOUR"] == "12566463"


def test_flag_sizes_take_ctm_flag_genies_only(fx_install, tmp_path):
    """looks for each flag with a helper(name, 3, 2): a .ctm genie, never
    a .ctl symbol of that name (the resolver's own lookup would take one). The alarm modes'
    flags count as the priorities' do, and a pair is measured once. (A project of its own:
    the fixture's Include holds the fxprio flags as genies too.)"""
    import shutil
    from ctgedit.format import dbfschema as S
    from ctgedit.format.symbols import SymbolReference, SymbolResolver

    proj = tmp_path / "inst" / "User" / "FxFlags"  # (no Include, no sibling install)
    proj.mkdir(parents=True)
    shutil.copyfile(fx_install.file("Include", "fxprio.ctl"), str(proj / "fxprio.ctl"))
    shutil.copyfile(fx_install.file("Include", "internal.ctm"), str(proj / "internal.ctm"))
    S.create_table(
        str(proj / "almprior.DBF"),
        "almprior",
        [
            {"PRIORITY": "2", "LIBNAME": "fxprio", "LIBOBJNAME": "fx_p2"},
            {"PRIORITY": "1", "LIBNAME": "internal", "LIBOBJNAME": "_stub_reserved"},
        ],
    )
    S.create_table(
        str(proj / "almmodes.DBF"),
        "almmodes",
        [
            {"DISPNAME": "Fx B", "LIBNAME": "internal", "LIBOBJNAME": "_stub_reserved"},
            {"DISPNAME": "Fx A", "LIBNAME": "fxprio", "LIBOBJNAME": "fx_shelved"},
        ],
    )
    page = str(proj / "x.ctg")
    res = SymbolResolver.for_page(page)
    assert AI.flag_refs(page) == [
        ("internal", "_stub_reserved"),
        ("fxprio", "fx_p2"),
        ("fxprio", "fx_shelved"),
    ]
    p2 = SymbolReference("fxprio", "fx_p2", "ctm", "genie")
    assert res.resolve(p2) is not None and res.resolve(p2, strict=True) is None
    stub = res.resolve(SymbolReference("internal", "_stub_reserved", "ctm", "genie"), strict=True)
    assert AI.flag_sizes(page, res) == [AI.DEFAULT_FLAG, (stub.width, stub.height)]


def test_flag_refs_read_the_pages_own_project(tmp_path):
    """Alarm priorities come from the page's own project."""
    from ctgedit.format import dbfschema as S

    user = tmp_path / "inst" / "User"
    own, inc = user / "FxOwn", user / "FxInc"
    for d in (own, inc):
        d.mkdir(parents=True)
        S.create_table(str(d / "pages.DBF"), "pages", [])
    S.create_table(str(own / "include.DBF"), "include", [{"NAME": "FxInc"}])
    S.create_table(str(inc / "include.DBF"), "include", [])
    S.create_table(
        str(inc / "almprior.DBF"),
        "almprior",
        [{"PRIORITY": "1", "UNACKONFG": "0x00ff00", "LIBNAME": "fxprio", "LIBOBJNAME": "fx_p1"}],
    )
    page = str(own / "x.ctg")
    assert AI.flag_refs(page) == []
    assert AI.top_priority(page, own=True) == ((255, 0, 0), None)  # CtDraw's default red
    assert AI.top_priority(page) == ((0, 255, 0), ("fxprio", "fx_p1"))  # the runtime's
    S.create_table(
        str(own / "almprior.DBF"), "almprior", [{"PRIORITY": "2", "LIBNAME": "fxprio", "LIBOBJNAME": "fx_p2"}]
    )
    AI._PRIORITY_CACHE.clear()  # (a table changed: the 'refresh' empties it)
    assert AI.flag_refs(page) == [("fxprio", "fx_p2")]


def test_flag_refs_follow_ctdraws_set(monkeypatch, tmp_path):
    """flag_refs follows CtDraw's flag set."""
    rows = {
        "almprior": [
            {"PRIORITY": "3", "LIBNAME": "fxflags", "LIBOBJNAME": "fx_f2"},  # bucket 2
            {"PRIORITY": "1", "LIBNAME": "fxflags", "LIBOBJNAME": "fx_f1"},  # bucket 0
            {"PRIORITY": "2", "LIBNAME": "", "LIBOBJNAME": ""},  # bucket 7
            {"PRIORITY": "4", "LIBNAME": "FXFLAGS", "LIBOBJNAME": "FX_F1"},  # dropped
            {"PRIORITY": "0", "LIBNAME": "fxflags", "LIBOBJNAME": "fx_f3"},
        ],  # no priority
        "almmodes": [
            {"DISPNAME": "Fx a", "LIBNAME": "fxflags", "LIBOBJNAME": "fx_f5"},  # bucket 0
            {"DISPNAME": "Fx c", "LIBNAME": "fxprio", "LIBOBJNAME": "fx_shelved"},  # bucket 7
            {"DISPNAME": "Fx B", "LIBNAME": "fxflags", "LIBOBJNAME": "fx_f6"},
        ],
    }  # bucket 2
    monkeypatch.setattr(AI, "_project_rows", lambda _page, table: rows[table])
    # (the set's list: f1 | f1 - | f1 - f2 | f1 - f6 f2 ('Fx B' < 'Fx a') |
    # f5 f1 - f6 f2 | f5 f1 shelved - f6 f2)
    assert AI.flag_refs(str(tmp_path / "x.ctg")) == [
        ("fxflags", "fx_f5"),
        ("fxflags", "fx_f1"),
        ("fxprio", "fx_shelved"),
        ("fxflags", "fx_f6"),
        ("fxflags", "fx_f2"),
    ]
    rows["almprior"] = []
    assert AI.flag_refs(str(tmp_path / "y" / "x.ctg")) == []  # (its own cache key)


def _ai_bounds_match(paths):
    from ctgedit.editing import EditableDocument

    n = 0
    for name, path in paths:
        doc = EditableDocument.open(path)
        objs = doc.parsed.page.objects
        for i, po in enumerate(objs):
            if po.type_code != 0x08 or not po.class_flag:
                continue
            s = AI.settings(AI.props_of(po))
            if not s["on"]:
                continue
            kids = doc._direct_children(i)
            _u1, u2 = doc._union_rects(kids)
            assert tuple(doc._alarm_bounds(i, u2)) == _rect2(doc, i), (name, i)
            n += 1
    return n


#: the fixture defect the alarm indicator tests wait for
NEED_AI = (
    "needs fixture: alarm indicator groups that are ON (AI_ON 1): FxMain's FxAlarms "
    "FxAi01 .. FxAi30, FxAnim's FxAlarmGroup and fxparts.fx_alarm all store AI_ON 0 "
    "(tools/make_fixtures.py passes bag keys, AI_ON / AI_WIDTH ..., to "
    "set_alarm_indicator, which takes the page's values: has, expr, width, padding, "
    "inside, flag_on)"
)


def _ai_on_groups(path):
    from ctgedit.format.page import parse_ctg

    return sum(
        1
        for po in parse_ctg(path).page.objects
        if po.type_code == 0x08 and po.class_flag and AI.settings(AI.props_of(po))["on"]
    )


def test_ai_bounds_match_the_stored_bounds(fx_page):
    """Computed AI bounds match the stored ones."""
    path = fx_page("FxAlarms")
    if not _ai_on_groups(path):
        pytest.skip(NEED_AI)
    assert _ai_bounds_match([("FxAlarms", path)]) >= 30


@pytest.mark.golden
def test_ai_bounds_match_ctdraws(fx_install):
    """The Bounds CtDraw's load + save stores for the same groups."""
    path = _oracle_page(fx_install, "pages", "FxMain/FxAlarms", "FxAlarmsCtDraw")
    if not _ai_on_groups(fx_install.page("FxAlarms")):
        pytest.skip(NEED_AI)
    assert _ai_bounds_match([("FxAlarms", path)]) >= 30


def test_alarm_indicator_legacy_advice(page):
    """Help 1172183: an indicator ignores V3/V4 objects -- a group of those
    gets the advice, a group of buttons (a current class, 0x1001) does not."""
    doc = _doc(page)
    b1 = doc.create_object("button", rect=(100, 5, 160, 30))
    b2 = doc.create_object("button", rect=(170, 5, 230, 30))
    g = doc.group_objects([b1, b2])
    ai = _sheet(doc, g).alarm_editor
    ai.has.setChecked(True)
    assert ai.hint.isHidden()
    s1 = doc.create_object("string", rect=(300, 5, 360, 30))
    s2 = doc.create_object("string", rect=(370, 5, 430, 30))
    g2 = doc.group_objects([s1, s2])
    ai = _sheet(doc, g2).alarm_editor
    ai.has.setChecked(True)
    assert not ai.hint.isHidden() and "3.xx/4.xx" in ai.hint.text()


def test_alarm_indicator_page(page):
    """Ticking Has Alarm Indicator (width 3, padding 2) writes CtDraw's full
    key set, makes the group dynamic (a pgalmind row), grows its Bounds to
    the frame + flag, and the design-time adorner is drawn with those
    settings; unticking gives the union back."""
    from ctgedit.io import pgregister

    doc = _doc(page)
    a = doc.create_object("rectangle", rect=(100, 5, 140, 40), colour=0x336699)
    b = doc.create_object("rectangle", rect=(150, 5, 190, 40), colour=0x336699)
    g = doc.group_objects([a, b])
    union = _rect2(doc, g)
    dlg = _sheet(doc, g)
    ai = dlg.alarm_editor
    assert not ai.has.isChecked() and not ai.width.isEnabled()
    assert dlg.apply() is False  # nothing changed: nothing written
    ai.has.setChecked(True)
    assert ai.width.isEnabled() and ai.padding.isEnabled()
    ai.width.setValue(3)
    ai.padding.setValue(2)
    ai.inside.setChecked(True)
    assert not ai.padding.isEnabled()  # CtDraw: Is Inside greys Padding
    ai.inside.setChecked(False)
    ai.expr.setText("Plant.Area1.Pump1")
    ai.scope.setCurrentIndex(0)
    ai.positions[5].click()  # Top, Right
    assert dlg.tab_marked("Alarm Indicator")
    n = len(doc._undo)
    assert dlg.apply() and len(doc._undo) == n + 1
    rp = {k: v for k, v in doc.object_properties(g).items() if k.startswith("AI_")}
    assert rp == {
        "AI_ON": "1",
        "AI_EXPR": "Plant.Area1.Pump1",
        "AI_EQHIER": "0",
        "AI_INCLUDE": "0",
        "AI_INSIDE": "0",
        "AI_WIDTH": "3",
        "AI_PADDING": "2",
        "AI_COLOUR": "12566463",
        "AI_FLAG_ON": "1",
        "AI_FLAG_LOC": "5",
    }
    po = doc.parsed.page.objects[g]
    assert po.subtype_size & 0x10
    s = AI.settings(AI.props_of(po))
    # (each priority's flag genie: Include's fxprio.ctm flags, 20 x 12, 24 x 14, 16 x 10)
    sizes = AI.flag_sizes(doc.path, doc._resolver())
    assert _rect2(doc, g) == AI.grown_bounds(union, s, sizes)
    assert _rect2(doc, g) != union
    rows = pgregister.page_rows(doc.serialize(), PAGE)["pgalmind"]
    mine = [rw for rw in rows if rw["AN"] == str(doc.page.objects[g].an)]
    assert mine and mine[0]["EXPR"] == "Plant.Area1.Pump1" and mine[0]["FLAG_LOC"] == "5"
    ind = doc.page.objects[g].raw["_alarm_indicator"]
    outer, inner, strip = AI.frame(union, s)
    assert (ind["outer"], ind["inner"], ind["strip"]) == (outer, inner, 4)
    assert ind["pen"] == (0xBF, 0xBF, 0xBF)
    # a member moved: the group's Bounds follow, frame included
    doc.move_objects([doc.subtree(g)[1]], 0, 10)
    kids = doc._direct_children(g)
    assert _rect2(doc, g) == AI.grown_bounds(doc._union_rects(kids)[1], s, sizes)
    # unticked: AI_ON 0 (the other keys kept, as CtDraw writes them), union back
    dlg = _sheet(doc, g)
    dlg.alarm_editor.has.setChecked(False)
    assert dlg.apply()
    assert doc.object_properties(g)["AI_ON"] == "0"
    assert _rect2(doc, g) == doc._union_rects(kids)[1]
    assert "_alarm_indicator" not in doc.page.objects[g].raw


def test_alarm_indicator_refuses_genie_syntax_on_a_placed_genie(page):
    doc = _doc(page)  # (FxLib's fxgenie.fx_pump resolves)
    g, _m = _genie(doc)
    dlg = _sheet(doc, g)
    ai = dlg.alarm_editor
    ai.has.setChecked(True)
    ai.expr.setText("%Equipment%")
    n = len(doc._undo)
    assert dlg.apply() is False and len(doc._undo) == n
    assert "genie syntax" in dlg._status.text()
    ai.expr.setText("FxPlant.Mixer")
    union = _rect2(doc, g)
    assert dlg.apply()
    rp = doc.object_properties(g)
    assert rp["AI_ON"] == "1" and rp["AI_EXPR"] == "FxPlant.Mixer"
    po = doc.parsed.page.objects[g]
    assert po.subtype_size & 0x10 and po.subtype_size & 0x200
    # the instance root's own indicator: the value bag only (the corpus'
    # CtDraw-saved roots), and the Bounds grown by frame + flag (live rule)
    r = GP.decode(po)
    assert any(k == "AI_ON" for k, _v in r.entries[0][1].pairs)
    assert not any(k.startswith("AI_") for k, _v in r.entries[0][2].pairs)
    s = AI.settings(AI.props_of(po))
    sizes = AI.flag_sizes(doc.path, doc._resolver())
    assert _rect2(doc, g) == AI.grown_bounds(union, s, sizes) != union
    kids = doc._subtree_span(g)[1:]
    assert all(doc.parsed.page.objects[j].subtype_size & 0x10 for j in kids)
    grown = _rect2(doc, g)
    # a genie parameter edit RE-COMPOSES the instance (the composition gives the
    # root the children's union; editing._regrow_alarm_root): the root keeps
    # its indicator AND its grown Bounds
    key, value = next(iter(doc.genie_all_params(g).items()))
    assert doc.set_genie_binding(g, key, (value or "") + "X")
    assert doc.object_properties(g)["AI_ON"] == "1"
    assert _rect2(doc, g) == grown
    doc.undo()
    assert _rect2(doc, g) == grown and doc.object_properties(g)["AI_ON"] == "1"
    doc.undo()
    assert _rect2(doc, g) == union and "AI_ON" not in doc.object_properties(g)


def _normalisation_keeps_ai_bounds(lib, elements):
    from ctgedit.format import genie_norm as GN

    for el in elements:
        recs = GP.load_element(lib, el).records
        out = GN.normalise_records(recs, ext="ctm", lib_path=lib)
        ai = [
            k
            for k, po in enumerate(recs)
            if po.type_code == 0x08 and po.class_flag and AI.settings(AI.props_of(po))["on"]
        ]
        assert ai, el
        for k in ai + [0]:
            assert (
                G.parse_geom_header(out[k].body_raw).rect2 == G.parse_geom_header(recs[k].body_raw).rect2
            ), (el, k)


def test_element_normalisation_keeps_alarm_indicator_bounds(fx_install):
    """Element normalisation keeps alarm indicator bounds."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import element as E

    lib = fx_install.file("FxLib", "fxparts.ctm")
    d = ElementDocument.open_element(lib, "fx_mixed")
    k = next(
        i
        for i, o in enumerate(d.parsed.page.objects)
        if o.type_code == 0x08 and o.class_flag and d._template_record(i)
    )
    union = _rect2(d, k)
    assert d.set_alarm_indicator(k, {"has": True, "expr": "%Equipment%", "flag_on": False})
    assert _rect2(d, k) == (union[0] - 10, union[1] - 10, union[2] + 10, union[3] + 10)
    d.save()
    assert E.LibraryFile.open(lib).find("fx_mixed") is not None
    _normalisation_keeps_ai_bounds(lib, ["fx_mixed"])


@pytest.mark.golden
def test_ctdraw_stored_alarm_indicator_bounds_survive_the_normalisation(fx_install):
    """The same over an element CtDraw saved with an indicator on (it stores the union +
    W + 2 + pad): fxparts.fx_alarm as the oracle's elements capture of FxLib/fxparts.ctm
    holds it."""
    lib = os.path.join(fx_install.project("FxLib"), "fxparts_ctdraw.ctm")
    with open(lib, "wb") as fh:
        fh.write(fxinstall.oracle_bytes("elements", "FxLib/fxparts.ctm", "library.ctm"))
    recs = GP.load_element(lib, "fx_alarm").records
    if not any(po.type_code == 0x08 and po.class_flag and AI.settings(AI.props_of(po))["on"] for po in recs):
        pytest.skip(NEED_AI)
    _normalisation_keeps_ai_bounds(lib, ["fx_alarm"])


def test_genie_element_group_alarm_indicator(fx_install):
    """In a genie element, a group's indicator goes to its template bag."""
    from ctgedit.editing import ElementDocument

    lib = fx_install.file("FxLib", "fxparts.ctm")
    d = ElementDocument.open_element(lib, "fx_mixed")
    k = next(
        i
        for i, o in enumerate(d.parsed.page.objects)
        if o.type_code == 0x08 and o.class_flag and d._template_record(i)
    )
    union = _rect2(d, k)
    n = len(d._undo)
    assert d.set_alarm_indicator(k, {"has": True, "expr": "%Equipment%", "flag_on": False})
    assert len(d._undo) == n + 1
    r = GP.decode(d.parsed.page.objects[k])
    b2 = dict(r.entries[0][2].pairs)
    assert b2["AI_ON"] == "1" and b2["AI_EXPR"] == "%Equipment%"
    assert d.alarm_indicator(k)["expr"] == "%Equipment%"
    g = 3 + 2 + 5  # W + 2 + pad, no flag
    grown = (union[0] - g, union[1] - g, union[2] + g, union[3] + g)
    assert _rect2(d, k) == grown
    d.save()
    d2 = ElementDocument.open_element(lib, "fx_mixed")
    assert _rect2(d2, k) == grown and d2.alarm_indicator(k)["has"]
    d.undo()
    assert "AI_ON" not in d.object_properties(k) and _rect2(d, k) == union


def test_equipment_picker_lists_the_chain(fx_project):
    """FxMain's equipment (FxPlant and its tanks T201 .. T226)."""
    from ctgedit.ui.objprops.alarm_indicator import EquipmentPickerDialog
    from ctgedit.ui.objprops.expression import ExpressionContext

    _app()
    dlg = EquipmentPickerDialog(None, ExpressionContext(fx_project("FxMain")))
    assert dlg.model.rowCount() > 0
    assert dlg.select_text("FxPlant") and "FxPlant" in dlg.chosen()
    from ctgedit.ui.help import DIALOG_TOPICS

    assert "EquipmentPickerDialog" in DIALOG_TOPICS


# ------------------------------------------------------------------ Goto / AN count
def test_goto_finds_by_full_name(page):
    from ctgedit.ui.dialog.dialogs import GotoObjectDialog

    doc = _doc(page)
    c = doc.create_object("rectangle", rect=(120, 5, 160, 40), colour=0x336699)
    d = doc.create_object("rectangle", rect=(170, 5, 210, 40), colour=0x336699)
    g = doc.group_objects([c, d])
    member = [j for j in doc.subtree(g) if j != g][0]
    assert doc.set_object_name(g, "Skid") and doc.set_object_name(member, "Valve")
    other = doc.create_object("rectangle", rect=(220, 5, 260, 40), colour=0x336699)
    assert doc.set_object_name(other, "Valve")  # a second 'Valve', at the top level
    _app()
    dlg = GotoObjectDialog(doc)
    dlg._filter.setText("Valve")  # CtDraw's name prefix: both
    assert sorted(dlg.matches()) == sorted([member, other])
    dlg._filter.setText("skid.valve")  # the full name: the member only
    assert dlg.matches() == [member] and dlg.current_index() == member
    assert dlg._tree.currentItem().text(0).startswith("Valve  ")
    dlg._filter.setText("Other.Valve")  # no such full name: text fallback
    assert member not in dlg.matches()


def test_animation_point_count_in_the_status_bar(fx_page, monkeypatch):
    """FxPerf's FxLarge: 1,161 animation points (its pump and pipe genies, styled shapes,
    texts and numbers)."""
    from ctgedit.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    try:
        win.open_document(fx_page("FxLarge", "FxPerf"))
        n = win.doc.animation_point_count()
        assert 1000 < n < 3000
        assert win.lbl_ans.text() == "%d animation points" % n
        # (the limit is Settings > Appearance > Status bar's, ui/prefs/window_options.AN_WARN)
        from ctgedit.ui.prefs import window_options

        monkeypatch.setattr(window_options, "AN_WARN", 100)
        win._an_count_key = None
        win._update_an_count()
        tip = win.lbl_ans.toolTip()
        assert win.lbl_ans.text().startswith("\u26a0") and "above 100" in tip and "3000" in tip
    finally:
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.close()
