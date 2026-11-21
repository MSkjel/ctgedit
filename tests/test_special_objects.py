"""Special objects: Trend, Cicode object, Web Content, ActiveX."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.format import factory  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format import legacy as L  # noqa: E402
from ctgedit.format import webcontent as W  # noqa: E402

import fxinstall  # noqa: E402
from qtkit import app as _app

#: the host page: a copy of FxShapes in FxMain under this name
HOST = "FxSpecial"

NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton


def _copy(fx_install, folder=None):
    """A copy of FxShapes named HOST: in FxMain, or in ``folder`` (outside a project)."""
    folder = folder or fx_install.project("FxMain")
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(str(folder), HOST + ".ctg")
    shutil.copy2(fx_install.page("FxShapes"), dst)
    return dst


def _doc(fx_install, folder=None):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(fx_install, folder))
    doc.derive_check = True  # every derive checked against a full one
    return doc


def _sheet(doc, index, **kw):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index, **kw)


def _gh(doc, i):
    return G.parse_geom_header(doc.parsed.page.objects[i].body_raw)


# ------------------------------------------------------------------ the trend class section
def test_trend_class_section_pens_and_samples():
    """The 82-byte section: 8 (on, off) pen colours after
    the sample count, CtDraw's defaults on a new trend; the
    Samples apply keeps the RIGHT edge."""
    po = factory.create_object("trend", (120, 150, 300, 225))
    info = L.trend_info(po)
    assert info["spacing"] == 1 and info["samples"] == 181 and len(info["pens"]) == 8
    assert L.DEFAULT_PEN_COLOURS == (
        0x00FF00,
        0xFFFF00,
        0xFF0000,
        0x00FFFF,
        0xFF00FF,
        0xFFFFFF,
        0x0000FF,
        0xBFBFBF,
    )
    # a NEW trend has CtDraw's default pens (the seed's own order differs)
    assert info["pens"] == [(c, c) for c in L.DEFAULT_PEN_COLOURS]
    assert L.set_trend_pen_colours(po, [(0x123456, 0x654321), None, (0xFF000000, 0xFF000000)])
    info = L.trend_info(po)
    assert info["pens"][0] == (0x123456, 0x654321)
    assert info["pens"][2] == (0xFF000000, 0xFF000000)
    assert L.set_trend_samples(po, 60, 3) == (300 - 3 * 59, 150, 300, 225)
    info = L.trend_info(po)
    assert (info["samples"], info["spacing"]) == (60, 3)
    assert L.class_tail(po)[0] == "trend"  # still the engine's consistent section
    assert tuple(G.parse_geom_header(po.body_raw).rect1) == (123, 150, 300, 225)
    # a library copy has no section: nothing to write
    lib = factory.create_object("trend", (0, 0, 50, 50))
    lib.subtype_size &= ~0x2
    assert L.trend_info(lib) is None and not L.set_trend_pen_colours(lib, [(1, 1)])


def test_trend_sheet_stores_pens_cluster_periodic_command_and_samples(fx_install):
    """The Trend page: PEN<n> (empty removes), the pen colours (class section),
    CLUSTER, PERIODIC TRUE / FALSE, CMD, samples / pixels -- ONE undo step --
    and the pgtrend row the compiler reads carries them."""
    from ctgedit.io import pgregister

    doc = _doc(fx_install)
    t = doc.create_object("trend", rect=(100, 100, 399, 299))
    dlg = _sheet(doc, t)
    assert dlg.page_ids()[:2] == ["general", "visibility"]
    ed = dlg.trend_editor
    assert ed.cluster.lineEdit().maxLength() == 16 and ed.pen_tag.maxLength() == 79
    # the pen's Wizard = Insert Trend (help 1172143: trend tags of the include chain)
    assert [a.text() for a in ed.pen_tag._menu.actions()] == ["Insert Trend Tag…"]
    # all 8 pens in view at once
    assert all(ed.table.rowHeight(r) == 22 for r in range(8))
    ed.set_pen(0, "Tank1_Level", (0x0000FF, 0x0000FF))
    ed.set_pen(1, "Tank2_Level")
    ed.cluster.setCurrentText("Cluster1")
    ed.periodic.setChecked(True)
    ed.command.setText("TrendRun();")
    ed.samples.setValue(100)
    ed.spacing.setValue(2)
    n = len(doc._undo)
    assert dlg.apply() is True and dlg._status.isHidden()
    assert len(doc._undo) == n + 1
    props = doc.object_properties(t)
    assert props["PEN1"] == "Tank1_Level" and props["PEN2"] == "Tank2_Level"
    assert "PEN3" not in props
    assert props["PERIODIC"] == "TRUE" and props["CLUSTER"] == "Cluster1"
    assert props["CMD"] == "TrendRun();"
    tp = doc.trend_properties(t)
    assert tp["pens"][0][1] == (0x0000FF, 0x0000FF)
    assert (tp["samples"], tp["spacing"]) == (100, 2)
    assert tuple(_gh(doc, t).rect1) == (399 - 2 * 99, 100, 399, 299)
    row = pgregister.page_rows(doc.serialize(), HOST)["pgtrend"][-1]
    assert (row["PEN1"], row["PEN2"], row["PERIODIC"], row["CLUSTER"]) == (
        "Tank1_Level",
        "Tank2_Level",
        "TRUE",
        "Cluster1",
    )
    # a pen emptied again removes its key; the undo puts the trend back
    ed.set_pen(1, "")
    assert dlg.apply() is True and "PEN2" not in doc.object_properties(t)
    doc.undo()
    doc.undo()
    assert doc.object_properties(t) == {}
    assert L.trend_info(doc.parsed.page.objects[t])["spacing"] == 1
    with pytest.raises(ValueError):
        doc.set_trend_properties(t, samples=0)
    with pytest.raises(ValueError):
        doc.set_trend_properties(t, spacing=33)
    with pytest.raises(ValueError):
        doc.set_trend_properties(t, cluster="C" * 17)


def test_trend_pen_advice_names_unknown_trend_tags():
    from ctgedit.ui.objprops import trend as T

    class Ctx:
        def trend_tags(self):
            class Tag:
                name = "Feed"

            return [Tag()]

    assert T._trend_tag_advice("Feed", "trend", Ctx()) == []
    assert T._trend_tag_advice("Feed.XUCL", "trend", Ctx()) == []  # an SPC pen
    assert T._trend_tag_advice("%Tag%.X", "trend", Ctx()) == []  # a genie parameter
    assert "Nope" in T._trend_tag_advice("Nope", "trend", Ctx())[0].message
    assert T._trend_tag_advice("Nope", "expr", Ctx()) == []


# ------------------------------------------------------------------ Web Content
def _web_records(path):
    from ctgedit.format.page import parse_ctg

    return [po for po in parse_ctg(path).page.objects if po.type_code == W.WEB_TC]


def test_web_string_section_is_the_corpus_layout(fx_install):
    """[u16 n][URL][u8 atoi(ENABLEPRINT)] before the 8-byte native rect
    (the string section): rebuilding it from the bags changes none of the stored records (FxLegacy's
    FxWeb: URL about:blank, ENABLEPRINT 1), and the .ctF ctgedit builds keeps the same
    section."""
    from ctgedit.format import ctf
    from ctgedit.format.page import parse_ctg_bytes

    page = fx_install.page("FxLegacy")
    web = _web_records(page)
    assert len(web) == 1
    po = web[0]
    props = po.raw_properties()
    assert W.read_string_section(po) == ("about:blank", 1)
    assert props.get(W.URL_KEY) == "about:blank" and props.get("ENABLEPRINT") == "1"
    assert W.rebuild_string_section(po) is False
    with open(page, "rb") as fh:
        built = parse_ctg_bytes(ctf.build_ctf(fh.read(), page, bake=False))
    web = [p for p in built.page.objects if p.type_code == W.WEB_TC]
    assert len(web) == 1 and W.read_string_section(web[0]) == ("about:blank", 1)


@pytest.mark.golden
def test_web_string_section_is_what_ctdraw_writes(fx_install):
    """CtDraw's PageOpen + PageSave of FxLegacy (the 'pages' unit FxMain/FxLegacy): its
    web content record's string section is the one rebuilt from its bags, and the .ctF
    it wrote keeps the same section."""
    import shutil as _sh

    seen = 0
    for name in ("page.ctg", "page.ctF"):
        src = fxinstall.oracle("pages", "FxMain/FxLegacy", name)
        dst = os.path.join(fx_install.project("FxMain"), "FxOracleLegacy" + name[4:])
        _sh.copyfile(src, dst)
        for po in _web_records(dst):
            url, flag = W.read_string_section(po)
            assert (url, flag) == ("about:blank", 1)
            if name == "page.ctg":
                assert url == po.raw_properties().get(W.URL_KEY, "")
                assert W.rebuild_string_section(po) is False
            seen += 1
    assert seen == 2


def test_web_content_sheet_url_print_and_the_runtime_section(fx_install):
    """The Web Content page: URL.NonGenie (2048) + ENABLEPRINT '1' / no key; the
    string section (what the runtime's .ctF shows) follows; advice for an
    incomplete / non-https URL; the LOSS OF CONTROL message is on the page."""
    from ctgedit.format import ctf
    from ctgedit.format.page import parse_ctg_bytes

    doc = _doc(fx_install)
    w = doc.create_object("web_content", rect=(10, 10, 410, 310))
    dlg = _sheet(doc, w)
    assert dlg.page_ids() == [
        "general",
        "visibility",
        "move_h",
        "move_v",
        "scale_h",
        "scale_v",
        "access",
        "metadata",
    ]
    ed = dlg.web_editor
    texts = [lab.text() for lab in ed.widget.findChildren(QtWidgets.QLabel)]
    assert any("LOSS OF CONTROL" in t for t in texts)
    # help 1172149: Tool tip and Log device greyed; the name (DspWebContentSetURL) editable
    enabled = {
        k: [w_.isEnabled() for _f, w_ in dlg._widgets.get(k, [])]
        for k in ("__AN_NAME__", "COMMENT", "DEVICE")
    }
    assert enabled == {"__AN_NAME__": [True], "COMMENT": [False], "DEVICE": [False]}
    ed.url.setPlainText("www.aveva.com")
    assert not ed.advice.isHidden() and "protocol" in ed.advice.text()
    ed.url.setPlainText("http://localhost:5000/")
    assert "https" in ed.advice.text()
    ed.url.setPlainText("https://www.aveva.com/")
    assert ed.advice.isHidden()
    ed.enable_print.setChecked(True)
    n = len(doc._undo)
    assert dlg.apply() is True and len(doc._undo) == n + 1
    assert doc.web_content(w) == {"url": "https://www.aveva.com/", "enable_print": True}
    po = doc.parsed.page.objects[w]
    assert W.read_string_section(po) == ("https://www.aveva.com/", 1)
    data = doc.serialize()
    built = parse_ctg_bytes(ctf.build_ctf(data, doc.path, bake=False))
    web = [p for p in built.page.objects if p.type_code == W.WEB_TC]
    assert web and W.read_string_section(web[-1]) == ("https://www.aveva.com/", 1)
    # print off removes the key; an empty URL removes URL.NonGenie
    assert doc.set_web_content(w, url="", enable_print=False) is True
    assert W.URL_KEY not in doc.object_properties(w)
    assert "ENABLEPRINT" not in doc.object_properties(w)
    assert W.read_string_section(doc.parsed.page.objects[w]) == ("", 0)
    with pytest.raises(ValueError):
        doc.set_web_content(w, url="https://x/" + "a" * 2048)


# ------------------------------------------------------------------ Cicode object
def test_cicode_object_command_display_symbol_and_extent(fx_install):
    """The Cicode page: CMD (254, multi-line stored CRLF; empty = an animation
    point, no key), SYMBOL (default = no key); the object's drawn extent is
    its symbol's native rect with the element origin on its point, else the
    f(x) icon. The copy's include chain is FxMain's."""
    from ctgedit.format import placement

    doc = _doc(fx_install)
    c = doc.create_object("cicode_object", rect=(300, 300, 331, 317))
    # a new one is an animation point: no CMD key (the corpus never stores CMD '')
    assert doc.object_properties(c) == {}
    dlg = _sheet(doc, c)
    assert dlg.page_ids() == ["general", "keyboard", "access"]
    ed = dlg.cicode_editor
    ed.command.setPlainText('hFont = DspFontHnd("Segoe UI");\nDspStr(DspGetAnCur(), hFont, "x")')
    assert dlg.apply() is True
    assert (
        doc.cicode_object(c)["command"]
        == 'hFont = DspFontHnd("Segoe UI");\r\nDspStr(DspGetAnCur(), hFont, "x")'
    )
    assert dlg.tab_marked("Cicode")
    gh = _gh(doc, c)
    icon = tuple(a - b for a, b in zip(gh.rect2, tuple(gh.anchor) * 2))
    assert icon == L.RECT2_DELTAS[0x1009]
    res = doc._resolver()
    info = placement.element_info(res, "cicode", "default", "ctl") if res else None
    assert info is not None  # Include's cicode.ctl (the stand-in)
    assert info["root_tc"] == 0x07  # a bitmap
    assert doc.set_cicode_object(c, symbol="cicode.default") is False  # = the default
    from ctgedit.ui.objprops.cicode import symbol_problem

    assert symbol_problem(doc, "cicode.default") is None
    assert "not in a library" in symbol_problem(doc, "nolib.nothing")
    assert "bitmap" in symbol_problem(doc, "fxsym.fx_group")  # a group
    assert doc.set_cicode_object(c, command="") is True
    assert "CMD" not in doc.object_properties(c)
    with pytest.raises(ValueError):
        doc.set_cicode_object(c, command="x" * 255)
    with pytest.raises(ValueError):
        doc.set_cicode_object(c, symbol="nodot")


def test_the_default_cicode_symbol_is_the_icons_extent(fx_install):
    """Include's cicode.ctl 'default' (a bitmap) has the f(x) icon's extent exactly about
    its origin (RECT2_DELTAS[0x1009]: the icon's bottom left on the point)."""
    from ctgedit.format import placement

    doc = _doc(fx_install)
    res = doc._resolver()
    info = placement.element_info(res, "cicode", "default", "ctl") if res else None
    assert info is not None and info["root_tc"] == 0x07
    (ex, ey), n = info["e"], info["N"]
    assert (n[0] - ex, n[1] - ey, n[2] - ex, n[3] - ey) == L.RECT2_DELTAS[0x1009]


def test_cicode_object_symbol_moves_its_extent(fx_install):
    """A bitmap symbol on a Cicode object: rect2 = the element's native rect
    with its origin on the object's point; back to the icon
    with the default."""
    from ctgedit.format import placement

    doc = _doc(fx_install)
    res = doc._resolver()
    lib, el = "fxsym", "fx_bitmap"  # FxLib's fxsym.ctl (a bitmap)
    info = placement.element_info(res, lib, el, "ctl") if res else None
    assert info is not None and info["root_tc"] == 0x07
    c = doc.create_object("cicode_object", rect=(300, 300, 331, 317))
    assert doc.set_cicode_object(c, symbol="%s.%s" % (lib, el)) is True
    gh = _gh(doc, c)
    (ex, ey), n = info["e"], info["N"]
    ax, ay = gh.anchor
    assert tuple(gh.rect2) == (
        int(n[0] - ex + ax),
        int(n[1] - ey + ay),
        int(n[2] - ex + ax),
        int(n[3] - ey + ay),
    )
    assert doc.set_cicode_object(c, symbol="") is True
    gh = _gh(doc, c)
    assert tuple(a - b for a, b in zip(gh.rect2, tuple(gh.anchor) * 2)) == L.RECT2_DELTAS[0x1009]


def test_cicode_object_symbol_that_does_not_resolve_keeps_its_extent(fx_install):
    """A symbol this page cannot resolve (a page opened outside its project has no include
    chain) leaves the object's drawn extent as stored. '<default>' (stored literally, as
    CtDraw does) is the icon. The copy is outside every project of the install."""
    loose = os.path.join(os.path.dirname(os.path.dirname(fx_install.root)), "loose")
    doc = _doc(fx_install, folder=loose)
    c = doc.create_object("cicode_object", rect=(300, 300, 331, 317))

    def rel():
        gh = _gh(doc, c)
        return tuple(a - b for a, b in zip(gh.rect2, tuple(gh.anchor) * 2))

    assert rel() == L.RECT2_DELTAS[0x1009]
    po = doc.parsed.page.objects[c]
    from ctgedit.format import legacy

    legacy.set_point_extent(po, (-5, -5, 60, 40))  # as a resolved symbol left it
    doc._touch_model([c])
    assert rel() == (-5, -5, 60, 40)
    assert doc.set_cicode_object(c, symbol="nolibrary.nosymbol") is True
    assert doc.object_properties(c)["SYMBOL"] == "nolibrary.nosymbol"
    assert rel() == (-5, -5, 60, 40)
    assert doc.set_cicode_object(c, symbol="<default>") is True
    assert doc.object_properties(c)["SYMBOL"] == "<default>"
    assert rel() == L.RECT2_DELTAS[0x1009]


def test_cicode_object_and_trend_own_keyboard_commands_not_touch(fx_install):
    """Their sheets have Keyboard Commands, no Touch (page masks 0x80070c00 /
    0x81070c02): the key sequences are Keyboard CHILD records."""
    from ctgedit.format import inputs

    doc = _doc(fx_install)
    c = doc.create_object("cicode_object", rect=(300, 300, 331, 317))
    assert doc.input_owner_status(c) == (True, "")
    assert doc.set_input_commands(c, keys=[{"KEY_SEQ": "F2", "CMD": "Beep()"}]) is True
    kids = doc.keyboard_children(c)
    assert len(kids) == 1 and doc.parsed.page.objects[kids[0]].type_code == inputs.KEYBOARD_TC
    assert G.parse_geom_header(doc.parsed.page.objects[kids[0]].body_raw).anchor == _gh(doc, c).anchor
    with pytest.raises(ValueError):
        doc.set_input_commands(c, touch={"CMD": "Beep()"})
    t = doc.create_object("trend", rect=(100, 100, 300, 200))
    assert doc.input_owner_status(t)[0]


def test_trend_with_keyboard_commands_still_draws_and_resizes(fx_install):
    """A trend that owns a Keyboard child is still a trend, not a group: it
    draws its pens, and a resize keeps its class section (the samples follow
    the width) with the key sequence riding on its anchor."""
    _app()
    from ctgedit.io.reader import ObjectKind
    from ctgedit.render.scene import PageScene

    doc = _doc(fx_install)
    t = doc.create_object("trend", rect=(100, 100, 299, 199))
    assert doc.set_input_commands(t, keys=[{"KEY_SEQ": "F3", "CMD": "Beep()"}]) is True
    obj = doc.page.objects[t]
    assert obj.kind == ObjectKind.TREND and obj.members
    scene = PageScene()
    scene.set_page(doc.page)
    item = scene._items[t]
    assert isinstance(item, QtWidgets.QGraphicsItemGroup)
    paths = [c for c in item.childItems()[0].childItems() if isinstance(c, QtWidgets.QGraphicsPathItem)]
    assert len(paths) == 8  # the 8 default pens
    doc.set_object_rect(t, (150, 120, 349, 259))
    po = doc.parsed.page.objects[t]
    assert L.class_tail(po)[0] == "trend" and L.trend_info(po)["samples"] == 200
    k = doc.keyboard_children(t)[0]
    assert G.parse_geom_header(doc.parsed.page.objects[k].body_raw).anchor == _gh(doc, t).anchor


def test_trend_samples_that_leave_the_page_coordinates_are_refused(fx_install):
    """The Samples apply keeps the right edge; a width that would put the
    left edge below the i16 page coordinates is refused with a plain message,
    and nothing changes."""
    doc = _doc(fx_install)
    t = doc.create_object("trend", rect=(100, 100, 299, 199))
    before = doc.parsed.page.objects[t].body_raw
    with pytest.raises(ValueError, match="fewer samples"):
        doc.set_trend_properties(t, samples=32767, spacing=32)
    assert doc.parsed.page.objects[t].body_raw == before
    assert doc.set_trend_properties(t, samples=1000, spacing=32) is True
    assert tuple(_gh(doc, t).rect1) == (299 - 32 * 999, 100, 299, 199)


# ------------------------------------------------------------------ ActiveX
def test_activex_tag_association_page(fx_install):
    """The ActiveX tag association page: the control's properties from its type library,
    'tag|event' in ACTIVEX_<property> ('' event = <Property change
    notification>), Clear Property removes them all -- ONE undo step each."""
    doc = _doc(fx_install)
    a = doc.create_object("activex", rect=(400, 400, 560, 560))
    assert doc.ensure_property(a, "ACTIVEXEXT_*EVENTCLASS", "Meter_E")
    dlg = _sheet(doc, a)
    assert dlg.page_ids()[:2] == ["general", "tag_association"]
    ed = dlg.tag_association_editor
    if ed.members is not None:  # CiMeterX registered on this machine
        names = [m.name for m in ed.members.properties]
        assert "Value" in names and "AlarmAck" in names
        assert "Acknowledge" in ed.members.events
    ed.set_association("Value", "TT001")
    ed.set_association("AlarmAck", "Temp_Ack", "Acknowledge")
    n = len(doc._undo)
    assert dlg.apply() is True and len(doc._undo) == n + 1
    props = doc.object_properties(a)
    assert props["ACTIVEX_Value"] == "TT001|"
    assert props["ACTIVEX_AlarmAck"] == "Temp_Ack|Acknowledge"
    assert dlg.tab_marked("Appearance", "Tag Association")
    ed.clear()
    assert dlg.apply() is True
    assert not any(k.startswith("ACTIVEX_") for k in doc.object_properties(a))
    assert "ACTIVEXEXT_*EVENTCLASS" in doc.object_properties(a)  # not an association


def test_new_controls_carry_no_donor_identity():
    """The ActiveX seeds' Identification / tool tip belong to their donor pages
    (the meter's event class 'CiVBA_AN545', the Process Analyst's name
    'EquipPA'): a new control stores none (CtDraw's defaults 'AN<n>' /
    '<page>_AN<n>' then apply), and keeps its persisted OLE state."""
    from ctgedit.io.pgregister import ole_clsid
    from ctgedit.io.activex import PROCESS_ANALYST_CLSID

    for kind in ("activex", "process_analyst", "scheduler"):
        po = factory.create_object(kind, (0, 0, 160, 120), handle=7)
        props = po.raw_properties()
        assert not {"ACTIVEXEXT_*NAME", "ACTIVEXEXT_*EVENTCLASS", "COMMENT"} & set(props), kind
        assert ole_clsid(po.body_raw), kind
    pa = factory.create_object("process_analyst", (0, 0, 570, 320))
    assert ole_clsid(pa.body_raw) == PROCESS_ANALYST_CLSID


def test_unnamed_process_analyst_draws_its_chart_surface(fx_install):
    """The Process Analyst's light chart surface follows the control (its OLE
    CLSID), not only a 'PA' in its name: a new, unnamed one gets it too; the
    Scheduler is an opaque window-white box (CtDraw runs the control)."""
    _app()
    from ctgedit.render.scene import PageScene

    doc = _doc(fx_install)
    pa = doc.create_object("process_analyst", rect=(20, 20, 420, 260))
    sc = doc.create_object("scheduler", rect=(430, 20, 830, 260))
    assert "ACTIVEXEXT_*NAME" not in doc.object_properties(pa)
    scene = PageScene()
    scene.set_page(doc.page)
    assert scene._items[pa].brush().color().lightness() >= 230
    assert scene._items[sc].brush().color().lightness() == 255


def test_activex_compatible_types_are_ctdraws():
    """'List Property Types' and the Type Evaluation of the
    apply."""
    from ctgedit.io import activex as AX

    assert AX.compatible_types(8) == "STRING"
    assert AX.compatible_types(0xB) == "BCD, BYTE, DIGITAL, INT, LONG, LONGBCD, REAL, UINT"
    assert AX.compatible_types(9) is None  # an object: not associable
    m = AX.ControlMember("Value", vt=5, readable=True, writable=True)
    assert AX.tag_type_problem(m, "TT001", "REAL") is None
    assert "smaller" in AX.tag_type_problem(m, "TT002", "INT")
    assert "different" in AX.tag_type_problem(m, "Msg", "STRING")
    assert m.status == "Read/Write"


def test_insert_control_copied_from_a_project_page(fx_install):
    """Insert ActiveX Control: a control the project's pages use is placed
    with its persisted state (the same CLSID), its own settings left behind,
    on the dragged box -- and gets its activex.DBF row."""
    from ctgedit.io import activex as AX
    from ctgedit.io import pgregister

    donors = AX.project_controls(fx_install.project("FxMain"))
    assert donors  # FxLegacy's control
    d = donors[0]
    doc = _doc(fx_install)
    i = doc.create_control(d.path, d.index, (50, 60, 250, 180))
    po = doc.parsed.page.objects[i]
    assert po.type_code == 0x0D and pgregister.ole_clsid(po.body_raw) == d.clsid
    assert tuple(_gh(doc, i).rect1) == (50, 60, 250, 180)
    props = doc.object_properties(i)
    assert not any(k.startswith(("ACTIVEX_", "METADATA_", "!*")) for k in props)
    assert "ACTIVEXEXT_*NAME" not in props
    rows = pgregister.activex_rows(doc.serialize(), HOST)
    an = str(G.get_handle(po) + 1)
    assert any(r["AN"] == an and r["OBJ_GUID"] == d.clsid for r in rows)
    # the donor page changed since the list was made: refused, nothing added
    n = len(doc.parsed.page.objects)
    with pytest.raises(ValueError, match="changed"):
        doc.create_control(
            d.path, d.index, (50, 60, 250, 180), clsid="{00000000-0000-0000-0000-000000000000}"
        )
    assert len(doc.parsed.page.objects) == n
    assert doc.create_control(d.path, d.index, (0, 0, 90, 90), clsid=d.clsid.lower()) == n


def test_copied_control_is_not_locked():
    """A donor locked in the Graphics Builder (Edit > Lock, dwFlags 0x4: the
    scheduler of a page) gives an unlocked new control."""
    donor = factory.create_object("scheduler", (0, 0, 200, 100))
    donor.subtype_size |= 0x4
    po = factory.clone_control(donor, (10, 10, 210, 110), handle=5)
    assert not po.subtype_size & 0x4 and donor.subtype_size & 0x4
    assert tuple(G.parse_geom_header(po.body_raw).rect1) == (10, 10, 210, 110)


# ------------------------------------------------------------------ the tools
def _me(view, kind, sp, buttons=QtCore.Qt.NoButton, mods=NOMOD):
    vp = view.mapFromScene(QtCore.QPointF(*sp))
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), LEFT, buttons, mods)


def _drag(view, a, b):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, a, LEFT))
    for k in (1, 2, 3):
        t = k / 3
        view.mouseMoveEvent(
            _me(view, QtCore.QEvent.MouseMove, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), LEFT)
        )
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, b))
    QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(_copy(fx_install))
    w.resize(1300, 900)
    w.show()
    w.view.set_smart_guides(False)
    w.view.set_snap(False)
    w.view.reset_zoom()
    w.view.centerOn(300, 220)
    QtWidgets.QApplication.processEvents()
    yield w
    for dlg in list(w._dialogs.values()):
        dlg.reject()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


def _new(w):
    return len(w.doc.parsed.page.objects) - 1


def _sheet_open(w, i):
    QtWidgets.QApplication.processEvents()
    QtCore.QCoreApplication.sendPostedEvents()
    for _ in range(3):
        QtWidgets.QApplication.processEvents()
    return w._dialogs.get(("objprops", i))


def test_special_objects_are_canvas_tools_and_open_their_sheet(win):
    """Trend: drag the box (a drag N px wide = N samples); Cicode
    object: click its point; Web Content / Process Analyst / Scheduler: drag
    the box; each opens its sheet once placed (CtDraw's toolbox)."""
    win._keep_tool = win.view.KEEP_TOOL = False  # (off: back to Select after each)
    w = win
    for t in ("trend", "cicode_object", "web_content", "process_analyst", "scheduler"):
        assert w.tool_actions[t].isCheckable()
    w.tool_actions["trend"].trigger()
    assert w.view.tool() == "trend"
    _drag(w.view, (100, 100), (300, 200))
    t = _new(w)
    po = w.doc.parsed.page.objects[t]
    assert po.type_code == 0x1003 and w.view.tool() == "select"
    assert tuple(_gh(w.doc, t).rect1) == (100, 100, 299, 199)
    assert L.trend_info(po)["samples"] == 200
    dlg = _sheet_open(w, t)
    assert dlg is not None and dlg.trend_editor is not None
    dlg.reject()
    # a Cicode object: a click, its point where the user clicked
    w.tool_actions["cicode_object"].trigger()
    _drag(w.view, (60, 70), (60, 70))
    c = _new(w)
    assert w.doc.parsed.page.objects[c].type_code == 0x1009
    assert tuple(_gh(w.doc, c).anchor) == (60, 70)
    dlg = _sheet_open(w, c)
    assert dlg is not None and dlg.cicode_editor is not None
    dlg.reject()
    # a DRAG with the Cicode tool still places it at the press (no box)
    w.tool_actions["cicode_object"].trigger()
    _drag(w.view, (80, 400), (200, 460))
    c2 = _new(w)
    assert w.doc.parsed.page.objects[c2].type_code == 0x1009
    assert tuple(_gh(w.doc, c2).anchor) == (80, 400)
    _sheet_open(w, c2).reject()
    w.tool_actions["web_content"].trigger()
    _drag(w.view, (320, 80), (520, 230))
    web = _new(w)
    assert w.doc.parsed.page.objects[web].type_code == 0x13
    assert tuple(_gh(w.doc, web).rect1) == (320, 80, 520, 230)
    dlg = _sheet_open(w, web)
    assert dlg is not None and dlg.web_editor is not None
    dlg.reject()
    # a click with the web tool: its default size
    w.tool_actions["web_content"].trigger()
    _drag(w.view, (40, 300), (40, 300))
    web2 = _new(w)
    l, t0, r, b = _gh(w.doc, web2).rect1
    assert (l, t0, r - l, b - t0) == (40, 300, 480, 360)
    _sheet_open(w, web2).reject()


def test_insert_activex_control_chooser_arms_the_tool(win, monkeypatch):
    """Edit > Insert ActiveX Control: the chooser lists the carried controls
    (and the project's); the chosen one is placed by the next drag."""
    win._keep_tool = win.view.KEEP_TOOL = False  # (off: back to Select after each)
    from ctgedit.ui.dialog import activex_insert as AI

    w = win
    dlg = AI.InsertActiveXDialog(os.path.dirname(w.doc.path), w)
    labels = [dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())]
    assert any(t.startswith("Process Analyst") for t in labels)
    assert dlg.select("Process Analyst")
    dlg._accept()
    assert dlg.choice() == ("kind", "process_analyst")

    class Fake(AI.InsertActiveXDialog):
        def exec(self):
            self.select("Scheduler")
            self._accept()
            return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(AI, "InsertActiveXDialog", Fake)
    w._insert_activex_dialog()
    assert w.view.tool() == "activex" and w._pending_control == ("kind", "scheduler")
    _drag(w.view, (100, 320), (400, 520))
    i = _new(w)
    po = w.doc.parsed.page.objects[i]
    assert po.type_code == 0x0D and tuple(_gh(w.doc, i).rect1) == (100, 320, 400, 520)
    from ctgedit.io.pgregister import ole_clsid

    assert ole_clsid(po.body_raw) == ole_clsid(factory.create_object("scheduler").body_raw)
    assert w._pending_control is None  # back to Select
    _sheet_open(w, i).reject()


# ------------------------------------------------------------------ the design-time look
def test_trend_and_web_content_draw_like_the_graphics_builder(fx_install):
    """8 sine traces in the pen colours (a no-colour pen draws
    nothing) + the focus rectangle; the #6B6F73 frame on
    #F3F4F5, the globe, 'Web Content' and the URL."""
    _app()
    from ctgedit.render.scene import PageScene

    doc = _doc(fx_install)
    t = doc.create_object("trend", rect=(100, 100, 299, 199))
    doc.set_trend_properties(t, pens=[("A", (0x0000FF, 0x0000FF)), ("", (0xFF000000, 0xFF000000))])
    w = doc.create_object("web_content", rect=(320, 80, 520, 330))
    doc.set_web_content(w, url="https://www.aveva.com/")
    scene = PageScene()
    scene.set_page(doc.page)
    item = scene._items[t]
    paths = [c for c in item.childItems()[0].childItems() if isinstance(c, QtWidgets.QGraphicsPathItem)]
    assert len(paths) == 7  # pen 2 has no colour
    assert paths[0].pen().color() == QtGui.QColor(0, 0, 255)
    # (r + 1 - l) / spacing + 1 points from x = r leftwards, the last one
    # clipped at the left edge (CtDraw counts from the clip box)
    assert paths[0].path().elementCount() == 201
    assert paths[0].path().elementAt(0).x == 299.5
    web = scene._items[w]
    assert isinstance(web, QtWidgets.QGraphicsRectItem)
    assert web.brush().color() == QtGui.QColor(0xF3, 0xF4, 0xF5)
    assert web.pen().color() == QtGui.QColor(0x6B, 0x6F, 0x73)
    labels = [c for c in web.childItems()[0].childItems() if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)]
    assert [c.text() for c in labels] == ["Web Content", "https://www.aveva.com/"]
    # 11 pt Segoe UI: lfHeight -(96 x 11) / 72 = -14; the title bold, the URL not
    assert [(c.font().pixelSize(), c.font().bold()) for c in labels] == [(14, True), (14, False)]


def test_trend_pen_after_a_no_colour_pen_takes_its_phase(fx_install):
    """walks one buffer of 8 phase tables and advances it only
    for a pen it DRAWS: with pen 2 'no colour', pen 3 is drawn with pen 2's
    phase (count x 1 / 16), pen 4 with count x 2 / 16."""
    import math

    _app()
    from ctgedit.render.scene import PageScene

    doc = _doc(fx_install)
    t = doc.create_object("trend", rect=(100, 100, 259, 179))  # 160 samples, 80 high
    doc.set_trend_properties(t, pens=[("A", None), ("", (0xFF000000, 0xFF000000))])
    scene = PageScene()
    scene.set_page(doc.page)
    paths = [
        c for c in scene._items[t].childItems()[0].childItems() if isinstance(c, QtWidgets.QGraphicsPathItem)
    ]
    assert len(paths) == 7
    count, height = (259 + 1 - 100) // 1 + 1, (179 + 1) - 100
    half = height // 2
    table, angle = [], 0.0
    for _j in range(count):
        table.append(int(math.sin(angle) * half) + half)
        angle += 3.14159265359 * 2.0 / count
    firsts = [p.path().elementAt(0).y - 0.5 - 100 for p in paths]
    assert firsts[:3] == [table[0], table[count // 16], table[count * 2 // 16]]
    # the second drawn pen is pen 3 (red, the default colour of pen 3)
    assert paths[1].pen().color() == QtGui.QColor(255, 0, 0)


def test_registry_builds_every_special_page():
    """Every special class page has a builder."""
    from ctgedit.ui.objprops import modules as M
    from ctgedit.ui.objprops import registry as R

    for tc in (0x13, 0x1003, 0x1009, 0x0D):
        assert M.page_builder(R.OWN_PAGES[tc]) is not None, hex(tc)
    assert M.page_builder(R.PAGE_BY_ID["tag_association"]) is not None
    assert (R.OWN_PAGES[0x1009].tab, R.OWN_PAGES[0x1009].module) == ("Cicode", "cicode")


# ------------------------------------------------------- ActiveX property list
def test_control_property_list_mirrors_its_activex_keys(fx_install):
    """A control's property list mirrors its ACTIVEX keys."""
    from ctgedit.format import olecontrol

    doc = _doc(fx_install)
    a = doc.create_object("activex", rect=(400, 400, 560, 560))
    po = doc.parsed.page.objects[a]
    prog, _c, pairs = olecontrol.find_list(po.body_raw)[1:]
    assert prog == "CiMeterX.TechMeter" and pairs == []
    assert doc.set_activex_associations(a, [("Value", "FX_SPEED", "")])
    pairs = olecontrol.find_list(doc.parsed.page.objects[a].body_raw)[3]
    assert pairs == [("Value", "FX_SPEED|")]
    assert doc.ensure_property(a, "ACTIVEXEXT_*EVENTCLASS", "Meter_E")
    pairs = olecontrol.find_list(doc.parsed.page.objects[a].body_raw)[3]
    assert pairs == [("Value", "FX_SPEED|"), ("*EVENTCLASS", "Meter_E")]
    doc.undo()
    assert olecontrol.find_list(doc.parsed.page.objects[a].body_raw)[3] == [("Value", "FX_SPEED|")]


@pytest.mark.golden
def test_control_property_list_rebuild_is_a_no_op_on_ctdraws_save(fx_install):
    """Every control CtDraw saved (its PageOpen + PageSave of FxActiveX, the 'pages' unit
    FxMain/FxActiveX: 43 controls, some stored with a stale list by the generator, which
    CtDraw rewrites) already lists exactly its keys. The corpus form is in
    tests_corpus/test_special_objects.py."""
    from ctgedit.format import olecontrol
    from ctgedit.format.page import parse_ctg

    src = fxinstall.oracle("pages", "FxMain/FxActiveX", "page.ctg")
    dst = os.path.join(fx_install.project("FxMain"), "FxOracleActiveX.ctg")
    shutil.copyfile(src, dst)
    seen = 0
    for po in parse_ctg(dst).page.objects:
        if po.type_code == 0x0D and olecontrol.find_list(po.body_raw) is not None:
            before = po.body_raw
            assert olecontrol.rebuild_list(po) is False
            assert po.body_raw == before
            seen += 1
    assert seen >= 40


def test_insert_activex_control_selects_the_control_inserted_last(fx_install):
    """The control chosen last is selected the next time the window opens."""
    _app()
    from ctgedit.ui.dialog.activex_insert import InsertActiveXDialog

    folder = fx_install.project("FxMain")
    dlg = InsertActiveXDialog(folder)
    names = [dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())]
    assert len(names) >= 2
    dlg.tree.setCurrentItem(dlg.tree.topLevelItem(1))
    dlg._accept()
    again = InsertActiveXDialog(folder)
    assert again.tree.currentItem().text(0) == names[1]
    again.reject()
