"""The genie dialog lists what CtDraw's lists."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

PAGE = "FxParts"

#: the rows a dialog shows: the element's declared parameters, in declaration order (a
#: name with a trailing space and mixed case kept as declared)
ROWS = {
    "FxOrder1": ["buttonText ", "MixedCase", "lower", "UPPER", "Title"],  # fx_order
    "FxManyParams": ["Tag"] + ["P%02d" % n for n in range(1, 42)],  # fx_many
    "FxNamed1": ["Name", "Tag", "Spare"],  # fx_named
    "FxDefault1": ["Title", "Tag"],  # fx_default
    # fx_stale declares Tag only: the instance's old Equip binding is not a row
    "FxStale1": ["Tag"],
    "FxStale2": ["Tag", "Note"],  # fx_stale_note
}


@pytest.fixture()
def doc(fx_page):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(fx_page(PAGE))


def _named(doc, name):
    (i,) = doc.find_objects_by_name(name)
    return i


def test_dialog_rows_are_the_declared_parameters(doc, fx_install):
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import reference_from_object

    for name, rows in ROWS.items():
        i = _named(doc, name)
        assert list(doc.genie_all_params(i)) == rows, name
        ref = reference_from_object(doc.parsed.page.objects[i])
        lib = doc._resolver().resolve(ref).library_path
        assert list(GP.declared_params_of(lib, ref.element)) == rows, name


def test_dialog_values_are_the_instance_values(doc):
    """A declared parameter the instance does not bind is blank; a stale instance's old
    'Equip' binding (FxStale1: fx_stale no longer declares it) is not a row."""
    p = doc.genie_all_params(_named(doc, "FxOrder1"))
    assert p["MixedCase"] == "mixed" and p["Title"] == "Order"
    assert p["buttonText "] == "" and p["lower"] == "" and p["UPPER"] == ""
    i = _named(doc, "FxStale1")
    assert doc.genie_bindings(i).get("Equip") == "FxPlant.Pump1"  # stored ...
    assert dict(doc.genie_all_params(i)) == {"Tag": "FX_PUMP1"}  # ... but not a row
    p = doc.genie_all_params(_named(doc, "FxManyParams"))
    assert (p["Tag"], p["P01"], p["P05"]) == ("FX_PUMP1", "first", "fifth")
    assert p["P02"] == "" and p["P40"] == ""


def test_genie_without_parameters_has_no_rows(doc):
    """fxparts.fx_none declares none: CtDraw opens its object sheet."""
    assert list(doc.genie_all_params(_named(doc, "FxNone1"))) == []


def test_setting_an_unbound_declared_parameter_recomposes(doc):
    """Setting an unbound declared parameter recomposes the instance."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import reference_from_object

    i = _named(doc, "FxNamed1")
    n = len(doc.parsed.page.objects)
    was = len(doc.subtree(i))
    ref = reference_from_object(doc.parsed.page.objects[i])
    el = GP.load_element(doc._resolver().resolve(ref).library_path, ref.element, compose=True)
    assert doc.genie_all_params(i)["Spare"] == ""
    assert doc.set_genie_binding(i, "Spare", "FX_TEST_SPARE")
    assert doc.genie_all_params(i)["Spare"] == "FX_TEST_SPARE"
    carriers = [
        j
        for j in doc.subtree(i)
        if any("FX_TEST_SPARE" in v for v in doc.parsed.page.objects[j].raw_properties().values())
    ]
    assert len(carriers) >= 1
    assert len(doc.subtree(i)) == len(el.records)
    assert len(doc.parsed.page.objects) == n - was + len(el.records)
    doc.undo()
    assert doc.genie_all_params(i)["Spare"] == ""


def test_setting_an_unbound_parameter_recomposes_a_stale_instance(doc):
    """The same on an instance OLDER than its element: the record count follows the
    current element (the page's genie went from its stored records to the element's).
    FxStale2 (fxparts.fx_stale_note, which gained an ellipse after FxParts placed it) binds
    Tag only; two member texts show the unbound Note."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import reference_from_object

    i = _named(doc, "FxStale2")
    n = len(doc.parsed.page.objects)
    was = len(doc.subtree(i))
    ref = reference_from_object(doc.parsed.page.objects[i])
    el = GP.load_element(doc._resolver().resolve(ref).library_path, ref.element, compose=True)
    assert len(el.records) != was  # the instance is older
    assert dict(doc.genie_all_params(i)) == {"Tag": "FX_PUMP3", "Note": ""}
    assert doc.set_genie_binding(i, "Note", "FX_TEST_NOTE")
    assert doc.genie_all_params(i)["Note"] == "FX_TEST_NOTE"
    carriers = [
        j
        for j in doc.subtree(i)
        if any("FX_TEST_NOTE" in v for v in doc.parsed.page.objects[j].raw_properties().values())
    ]
    assert len(carriers) >= 2
    assert len(doc.subtree(i)) == len(el.records)
    assert len(doc.parsed.page.objects) == n - was + len(el.records)
    doc.undo()
    assert doc.genie_all_params(i)["Note"] == "" and len(doc.subtree(i)) == was


def test_element_window_hatch_and_hotspot(fx_install):
    """CtDraw's element window: 8x8 #C0C0C0 lattice on white and the hotspot
    marker at the element origin (fxparts.fx_offset: e = (40, 30)); neither in exports."""
    from PySide6 import QtCore, QtGui, QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.editing import ElementDocument
    from ctgedit.render.scene import PageScene

    doc = ElementDocument.open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_offset")
    assert doc.hotspot == (40, 30)
    hx, hy = doc.hotspot
    sc = PageScene()
    sc.set_page(doc.page)
    sc.set_element_mode(True, doc.hotspot)

    def shot():
        img = QtGui.QImage(40, 40, QtGui.QImage.Format_RGB32)
        img.fill(QtGui.QColor("#123456"))
        p = QtGui.QPainter(img)
        sc.render(p, QtCore.QRectF(0, 0, 40, 40), QtCore.QRectF(hx - 20, hy - 20, 40, 40))
        p.end()
        return img

    img = shot()
    assert img.pixel(20 - 7, 20) & 0xFFFFFF == 0x000000  # the left arm
    with sc.export_only(None):
        out = shot()
    assert out.pixel(20 - 7, 20) & 0xFFFFFF != 0x000000
    sc2 = PageScene()
    sc2.set_page(doc.page)
    sc2.set_element_mode(True, None)
    img = QtGui.QImage(16, 16, QtGui.QImage.Format_RGB32)
    p = QtGui.QPainter(img)
    # (the element covers 0..80 x 0..60: the lattice shows to its right)
    sc2.render(p, QtCore.QRectF(0, 0, 16, 16), QtCore.QRectF(96, 0, 16, 16))
    p.end()
    cols = {img.pixel(x, y) & 0xFFFFFF for x in range(16) for y in range(16)}
    assert cols == {0xC0C0C0, 0xFFFFFF}


def test_unbound_declared_parameter_shows_its_default(doc):
    """Live (a page): an instance that does not bind a declared parameter shows
    the DECLARED DEFAULT in CtDraw's dialog. FxDefault1 leaves fx_default's
    %Title=Pump% unbound."""
    p = doc.genie_all_params(_named(doc, "FxDefault1"))
    assert list(p.items()) == [("Title", "Pump"), ("Tag", "FX_PUMP2")]


def test_genie_parameter_edit_recomposes_text_the_runtime_draws(doc):
    """A genie parameter edit recomposes the text the runtime draws."""
    from ctgedit.format import genie_place as GP

    root = _named(doc, "FxNamed1")
    assert doc.genie_all_params(root)["Tag"] == "FX_T201"
    name0 = doc.genie_all_params(root)["Name"]
    label = next(
        j
        for j in doc.subtree(root)
        if doc.parsed.page.objects[j].type_code == 0x05
        and doc.parsed.page.objects[j].raw_properties().get("TXT_OFF") == name0
    )
    value = next(
        j
        for j in doc.subtree(root)
        if doc.parsed.page.objects[j].raw_properties().get("TXT_STR", "").endswith("_LT")
    )
    expr0 = doc.parsed.page.objects[value].raw_properties()["TXT_STR"]
    assert expr0 == "FX_T201_LT"
    assert doc.set_genie_binding(root, "Name", "TQ42")
    assert GP.decode(doc.parsed.page.objects[label]).cls[6:10] == b"TQ42"
    assert doc.parsed.page.objects[value].raw_properties()["TXT_STR"] == expr0
