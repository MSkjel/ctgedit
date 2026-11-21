"""Touch and keyboard commands as child records."""

import os
import shutil
import struct

import fxinstall
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.format import factory, inputs as IN, writer as W  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format.page import parse_ctg  # noqa: E402
from ctgedit.io import pgregister as PR  # noqa: E402
from qtkit import app as _app

#: the page most tests edit (a copy): FxMain's FxInputs
PAGE = "FxInputs"
#: the fake install's files (set by the autouse ``_fx`` fixture)
FX = {}


@pytest.fixture(autouse=True)
def _fx(fx_install):
    FX.clear()
    FX.update(install=fx_install, page=fx_install.page(PAGE), main=fx_install.project("FxMain"))
    yield


def _named(doc, name):
    found = doc.find_objects_by_name(name)
    assert len(found) == 1, name
    return found[0]


def _open(tmp_path, src=None):
    """A copy of ``src`` (default FxInputs) in tmp_path, opened."""
    from ctgedit.editing import EditableDocument

    src = src or FX["page"]
    dst = tmp_path / os.path.basename(src)
    shutil.copy(src, dst)
    return EditableDocument.open(str(dst))


def _new_rect(doc):
    return doc.create_object("rectangle", rect=(20, 20, 90, 60), colour=0x336699)


# --------------------------------------------------------------------------- records
def _children(objs, owner, tc):
    par = IN.parents(objs)
    return [k for k, o in enumerate(objs) if par[k] == owner and o.type_code == tc]


def _session_free(po):
    """``po``'s payload with every bag size overshoot set to 0."""
    from ctgedit.format import genie_place as GP

    r = GP.decode(po)
    assert W.object_payload(GP.to_po(GP.encode(r))) == W.object_payload(po)
    r = GP.decode(po)
    for entry in r.entries:
        for bag in entry:
            if bag is not None:
                bag.ov = 0
    return W.object_payload(GP.to_po(GP.encode(r)))


def _value_ov(po):
    from ctgedit.format import genie_place as GP

    return GP.decode(po).entries[0][1].ov


@pytest.mark.golden
def test_new_input_records_are_byte_identical_to_ctdraws():
    """New input records are byte-identical to CtDraw's."""
    objs = parse_ctg(fxinstall.oracle("draw", "touch_and_keys", "page.ctg")).page.objects
    owner = next(k for k, o in enumerate(objs) if o.type_code == 0x0F and o.class_flag)
    anchor = G.parse_geom_header(objs[owner].body_raw).anchor
    (touch,) = _children(objs, owner, IN.TOUCH_TC)
    (kb,) = _children(objs, owner, 0x100B)
    assert list(IN.values(objs[touch]).items()) == [("ACTION", "KEY_LBUTTON"), ("DOWN_CMD", "Beep()")]
    rec = factory.make_input_record(
        "touch", G.get_handle(objs[owner]), anchor, {"DOWN_CMD": "Beep()", "RPT_RATE": ""}
    )
    assert _value_ov(rec) == 11
    assert _session_free(rec) == _session_free(objs[touch])
    rec = factory.make_input_record(
        "keyboard", G.get_handle(objs[owner]), anchor, {"KEY_SEQ": "ENTER", "CMD": "WinFree()"}
    )
    assert _value_ov(rec) == 11
    assert _session_free(rec) == _session_free(objs[kb])


def test_new_touch_record_layout():
    rec = factory.make_input_record(
        "touch", 41, (100, 200), {"CMD": "A()", "DOWN_CMD": "B()", "MSG_LOGDN": "down"}
    )
    gh = G.parse_geom_header(rec.body_raw)
    assert rec.type_code == 0x1010 and rec.subtype_size == 0x1A and rec.an_str == "-1"
    assert G.get_handle(rec) == 41 and rec.class_flag == 0
    assert gh.anchor == (100, 200) and gh.rect1 == (100, 200, 101, 201)
    assert gh.rect2 == (100, 200, 133, 223)
    assert struct.unpack_from("<2h", rec.body_raw, len(rec.body_raw) - 4) == (100, 200)
    assert list(IN.values(rec).items()) == [
        ("ACTION", "KEY_LBUTTON"),
        ("RPT_RATE", "500"),
        ("CMD", "A()"),
        ("DOWN_CMD", "B()"),
        ("MSG_LOGDN", "down"),
    ]


def test_new_keyboard_record_keys_in_ctdraws_order():
    """CtDraw's own new keyboard record (live: COM
    PropertiesInputKeyboardPutEx(0, 'F4', 'Beep()', '5', '1', 'WP04 both') +
    PageSave): KEY_SEQ, CMD, AREA, PRIV, MSG_LOG; 'same area / privilege as
    the object' = no key."""
    rec = factory.make_input_record(
        "keyboard",
        41,
        (100, 200),
        {"KEY_SEQ": "F4", "CMD": "Beep()", "PRIV": "1", "MSG_LOG": "WP04 both", "AREA": "5"},
    )
    assert list(IN.values(rec).items()) == [
        ("KEY_SEQ", "F4"),
        ("CMD", "Beep()"),
        ("AREA", "5"),
        ("PRIV", "1"),
        ("MSG_LOG", "WP04 both"),
    ]
    rec = factory.make_input_record(
        "keyboard", 41, (100, 200), {"KEY_SEQ": "F2", "CMD": "Beep()", "AREA": "5", "MSG_LOG": "WP04 area"}
    )
    assert list(IN.values(rec)) == ["KEY_SEQ", "CMD", "AREA", "MSG_LOG"]
    # an edit of an existing record appends a new key (CtDraw: AREA added to
    # the F3 record [KEY_SEQ, CMD, PRIV, MSG_LOG] lands after MSG_LOG)
    old = factory.make_input_record(
        "keyboard", 41, (100, 200), {"KEY_SEQ": "F3", "CMD": "Beep()", "PRIV": "1", "MSG_LOG": "WP04 priv"}
    )
    new = IN.with_values(
        old,
        {"KEY_SEQ": "F3", "CMD": "Beep()", "AREA": "7", "PRIV": "1", "MSG_LOG": "WP04 priv"},
        IN.KEYBOARD_KEYS,
    )
    assert list(IN.values(new)) == ["KEY_SEQ", "CMD", "PRIV", "MSG_LOG", "AREA"]


# --------------------------------------------------------------------------- read
def test_a_genie_member_shows_its_touch_and_key_commands(tmp_path):
    """FxInputGenie's rectangle member (fxparts.fx_inputs) owns a touch record and six
    keyboard records."""
    doc = _open(tmp_path)
    g = _named(doc, "FxInputGenie")
    (m,) = [k for k in doc.subtree(g) if doc.objects[k].type_code == 0x0F]
    info = doc.input_commands(m)
    t = info["touch"]
    assert t["CMD"] == "FxSet(FX_MODE, 1)"
    assert t["DOWN_CMD"] == "FxSet(FX_MODE, 2)"
    assert t["REPEAT_CMD"] == "FxSet(FX_MODE, 3)"
    assert [k["KEY_SEQ"] for k in info["keys"]] == [
        "LBUTTON_DN",
        "LBUTTON_UP",
        "LBUTTON_DBL",
        "LBUTTON_CMD_DN",
        "LBUTTON_CMD_UP",
        "RBUTTON_UP",
    ]
    # a genie member: shown, but its commands come from the library element
    assert info["editable"] is False and "genie" in info["reason"]
    with pytest.raises(ValueError):
        doc.set_input_commands(m, touch={"CMD": "X()"})

    from ctgedit.ui.objprops.input import InputCommandsTab

    _app()
    tab = InputCommandsTab(doc, m)
    assert tab.command_edits["CMD"].value() == t["CMD"]
    assert tab.command_edits["DOWN_CMD"].value() == t["DOWN_CMD"]
    assert tab.command_edits["REPEAT_CMD"].value() == t["REPEAT_CMD"]
    assert tab.rate.value() == 500
    assert tab.key_list.count() == 6 and tab.command_edits["CMD"].isReadOnly()
    assert not tab.has_changes() and tab.apply() is False


# --------------------------------------------------------------------------- create
def test_add_touch_to_a_new_rect(tmp_path):
    doc = _open(tmp_path)
    n0 = len(doc.objects)
    r = _new_rect(doc)
    assert doc.objects[r].subtype_size & 0x10 == 0  # a static rect
    t = doc.add_input(r, "touch", {"CMD": "Beep()"})
    objs = doc.objects
    assert len(objs) == n0 + 2 and t == r + 1  # the child right after it
    assert objs[r].class_flag == 1 and objs[r].subtype_size & 0x10
    assert objs[t].type_code == 0x1010 and G.get_handle(objs[t]) == G.get_handle(objs[r])
    assert G.parse_geom_header(objs[t].body_raw).anchor == G.parse_geom_header(objs[r].body_raw).anchor
    an = str(G.get_handle(objs[r]) + 1)
    rows = PR.page_rows(doc.parsed, PAGE)
    assert [x for x in rows["pgtouch"] if x["AN"] == an] == [
        {"PAGE": PAGE, "AN": an, "ACTION": "KEY_LBUTTON", "RPT_RATE": "500", "CMD": "Beep()"}
    ]
    # the owner is dynamic: its own pgdynobj row, like CtDraw's static owners
    assert [x["TYPE"] for x in rows["pgdynobj"] if x["AN"] == an] == ["0x000F"]
    # ... and stays dynamic through a later edit of a non-dynamic bag key
    assert doc.ensure_property(r, "SZXMINPC", "0")
    assert doc.objects[r].subtype_size & 0x10
    # save + reopen: the forest and the command survive
    out = str(tmp_path / "saved.ctg")
    doc.save(out)
    from ctgedit.editing import EditableDocument

    re = EditableDocument.open(out)
    assert re.input_commands(r)["touch"]["CMD"] == "Beep()"
    assert re.objects[r].class_flag == 1


def test_touch_edits_and_removal_follow_ctdraws_rules(tmp_path):
    doc = _open(tmp_path)
    r = _new_rect(doc)
    doc.set_input_commands(
        r, touch={"CMD": "A()", "REPEAT_CMD": "B()", "RPT_RATE": "250", "MSG_LOGRPT": "rep"}
    )
    t = doc.touch_child(r)
    assert IN.values(doc.objects[t]) == {
        "ACTION": "KEY_LBUTTON",
        "RPT_RATE": "250",
        "CMD": "A()",
        "REPEAT_CMD": "B()",
        "MSG_LOGRPT": "rep",
    }
    # one key edit keeps the rest (RPT_RATE absent = kept)
    assert doc.set_input_property(t, "CMD", "C()")
    assert doc.input_commands(r)["touch"]["RPT_RATE"] == "250"
    assert doc.input_commands(r)["touch"]["CMD"] == "C()"
    # clearing every command removes the record; the static rect is not dynamic
    doc.set_input_commands(r, touch={})
    assert doc.touch_child(r) is None and doc.objects[r].class_flag == 0
    assert doc.objects[r].subtype_size & 0x10 == 0
    doc.undo()
    assert doc.touch_child(r) is not None and doc.objects[r].class_flag == 1


def _stale_box(doc, i, rect):
    """Store ``rect`` as text ``i``'s rect1 (a box an old save left: the text's measured
    extent differs from it)."""
    po = doc.parsed.page.objects[i]
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, G.parse_geom_header(po.body_raw).rect1_off, *rect)
    po.body_raw = bytes(body)
    doc._touch_model([i])


def test_input_edit_on_a_text_writes_the_measured_extent(tmp_path):
    """An input edit on a text writes the measured extent."""
    doc = _open(tmp_path)
    rect1 = lambda i: G.parse_geom_header(doc.objects[i].body_raw).rect1  # noqa: E731
    owner = _named(doc, "FxTouchText")
    measured = rect1(owner)
    assert measured == (20, 20, 78, 36)
    t = doc.create_object("text", rect=(400, 20, 470, 36), text="Version")
    fresh = rect1(t)
    _stale_box(doc, t, (fresh[0], fresh[1] - 5, fresh[2] + 89, fresh[3]))
    _stale_box(doc, owner, (measured[0] - 23, measured[1] - 5, measured[2], measured[3] + 1))
    stale = rect1(owner)
    assert stale != measured and rect1(t) != fresh
    doc.add_input(t, "touch", {"CMD": "Beep(1)"})
    assert rect1(t) == fresh
    doc.set_input_property(doc.touch_child(owner), "CMD", "Beep(2)")
    assert rect1(owner) == measured
    doc.undo()
    assert rect1(owner) == stale


@pytest.mark.golden
def test_the_measured_extent_is_ctdraws(fx_page):
    """CtDraw re-measures every text on load (oracle pages/FxMain/FxInputs: its PageOpen +
    PageSave of FxInputs): FxTouchText's box is the one ctgedit measured."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page(PAGE))
    i = _named(doc, "FxTouchText")
    eng = parse_ctg(fxinstall.oracle("pages", "FxMain/FxInputs", "page.ctg")).page.objects
    assert G.parse_geom_header(eng[i].body_raw).rect1 == G.parse_geom_header(doc.objects[i].body_raw).rect1


def test_double_click_touch_command_reaches_pgtouch(tmp_path):
    """The Touch page's fourth action, Double click: DBLCLK_CMD / MSG_LOGDBL (the
    pgtouch columns of the same names)."""
    doc = _open(tmp_path)
    r = _new_rect(doc)
    doc.set_input_commands(r, touch={"DBLCLK_CMD": "Open()", "MSG_LOGDBL": "opened"})
    an = str(G.get_handle(doc.objects[r]) + 1)
    row = [x for x in PR.page_rows(doc.parsed, PAGE)["pgtouch"] if x["AN"] == an]
    assert row == [
        {
            "PAGE": PAGE,
            "AN": an,
            "ACTION": "KEY_LBUTTON",
            "RPT_RATE": "500",
            "DBLCLK_CMD": "Open()",
            "MSG_LOGDBL": "opened",
        }
    ]


def test_two_keyboard_commands_give_two_pgkey_rows(tmp_path):
    doc = _open(tmp_path)
    r = _new_rect(doc)
    k1 = doc.add_input(r, "keyboard", {"KEY_SEQ": "ENTER", "CMD": "A()"})
    k2 = doc.add_input(
        r, "keyboard", {"KEY_SEQ": "### ENTER", "CMD": "Tag = Arg1", "PRIV": "2", "MSG_LOG": "set"}
    )
    assert (k1, k2) == (r + 1, r + 2) and doc.objects[r].class_flag == 2
    an = str(G.get_handle(doc.objects[r]) + 1)
    rows = [x for x in PR.page_rows(doc.parsed, PAGE)["pgkey"] if x["AN"] == an]
    assert rows == [
        {"PAGE": PAGE, "AN": an, "KEY_SEQ": "ENTER", "CMD": "A()"},
        {"PAGE": PAGE, "AN": an, "KEY_SEQ": "### ENTER", "CMD": "Tag = Arg1", "PRIV": "2", "MSG_LOG": "set"},
    ]
    assert list(IN.values(doc.objects[k2])) == ["KEY_SEQ", "CMD", "PRIV", "MSG_LOG"]
    # removing the FIRST keeps the second record untouched
    second = W.object_payload(doc.objects[k2])
    doc.remove_input(k1)
    assert [W.object_payload(doc.objects[c]) for c in doc.keyboard_children(r)] == [second]
    doc.undo()
    assert [k["KEY_SEQ"] for k in doc.input_commands(r)["keys"]] == ["ENTER", "### ENTER"]


def _trim_register(src_project, dst, page):
    """A project copy whose register tables hold only ``page``'s rows."""
    from ctgedit.format.dbf import Dbf, Ndx, create_ndx

    for f in ("pages.DBF", "changes.DBF", "PROJECT.CIT"):
        shutil.copy(PR._find_file(src_project, f), os.path.join(dst, f))
    for t in ("pgtouch", "pgkey", "pgdynobj"):
        d = Dbf(PR._find_file(src_project, t + ".DBF"), writable=False)
        n = PR._open_ndx(src_project, t)
        recs = [r for _i, r in PR._page_records(d, n, "PAGE", page)]
        if n is not None:
            n.close()
        with open(d.path, "rb") as fh:
            head = bytearray(fh.read(d.hlen))
        struct.pack_into("<I", head, 4, len(recs))
        out = os.path.join(dst, t + ".DBF")
        with open(out, "wb") as fh:
            fh.write(bytes(head) + b"".join(recs) + b"\x1a")
        create_ndx(os.path.join(dst, t + ".NDX"), "PAGE", d.fmap["PAGE"][1])
        d.close()
        dd = Dbf(out)
        with Ndx(os.path.join(dst, t + ".NDX")) as nx:
            for i in range(dd.nrec):
                nx.insert(dd.field(dd.read(i), "PAGE"), i + 1)
        dd.close()
    shutil.copy(os.path.join(src_project, page + ".ctg"), os.path.join(dst, page + ".ctg"))
    # (the page's include chain: the project's include.DBF)
    shutil.copy(PR._find_file(src_project, "include.DBF"), os.path.join(dst, "include.DBF"))


def _rows(dst, table, page, an):
    from ctgedit.format.dbf import Dbf

    d = Dbf(os.path.join(dst, table + ".DBF"), writable=False)
    try:
        return [d.decode(r) for _i, r in d.find("PAGE", page) if r[:1] != b"*" and d.field(r, "AN") == an]
    finally:
        d.close()


def test_save_registers_touch_and_keyboard_rows(tmp_path):
    """The normal save writes the pgtouch / pgkey rows (AN = the owner's);
    editing the second keyboard command rewrites only its row (the n-th
    child of an AN matches the n-th row)."""
    from ctgedit.editing import EditableDocument

    dst = str(tmp_path / "FxCopy")
    os.makedirs(dst)
    _trim_register(FX["main"], dst, PAGE)
    doc = EditableDocument.open(os.path.join(dst, PAGE + ".ctg"))
    text_an = str(G.get_handle(doc.objects[_named(doc, "FxTouchText")]) + 1)
    r = _new_rect(doc)
    doc.set_input_commands(
        r, touch={"CMD": "Beep()"}, keys=[{"KEY_SEQ": "ENTER", "CMD": "A()"}, {"KEY_SEQ": "F2", "CMD": "B()"}]
    )
    assert doc.can_undo()
    doc.save()
    an = str(G.get_handle(doc.objects[r]) + 1)
    assert [x["CMD"] for x in _rows(dst, "pgtouch", PAGE, an)] == ["Beep()"]
    assert [(x["KEY_SEQ"], x["CMD"]) for x in _rows(dst, "pgkey", PAGE, an)] == [
        ("ENTER", "A()"),
        ("F2", "B()"),
    ]
    assert [x["TYPE"] for x in _rows(dst, "pgdynobj", PAGE, an)] == ["0x000F"]
    # the untouched page rows (FxTouchText's WinFree touch) are still there
    assert [x["CMD"] for x in _rows(dst, "pgtouch", PAGE, text_an)] == ["WinFree();"]
    k2 = doc.keyboard_children(r)[1]
    doc.set_input_property(k2, "CMD", "C()")
    rep = PR.register_page(dst, PAGE, doc.serialize())
    assert rep["tables"]["pgkey"]["updated"] and not rep["tables"]["pgkey"]["appended"]
    assert not rep["tables"]["pgkey"]["deleted"]
    assert [(x["KEY_SEQ"], x["CMD"]) for x in _rows(dst, "pgkey", PAGE, an)] == [
        ("ENTER", "A()"),
        ("F2", "C()"),
    ]


def test_fast_display_file_keeps_the_new_records(tmp_path):
    """The runtime's .ctF rebuild carries the new touch / keyboard records
    the way CtDraw's own .ctF carries them (bags stripped: dwFlags 0x12,
    still children of their owner: the golden test below)."""
    from ctgedit.format import ctf as CTF
    from ctgedit.format.page import parse_ctg_bytes

    doc = _open(tmp_path)
    r = _new_rect(doc)
    doc.set_input_commands(r, touch={"CMD": "Beep()"}, keys=[{"KEY_SEQ": "ENTER", "CMD": "A()"}])
    blob = CTF.build_ctf(doc.serialize(), doc.path, resolver=doc._resolver(), page=doc.parsed.page)
    objs = parse_ctg_bytes(blob).page.objects
    tail = objs[-3:]
    assert [o.type_code for o in tail] == [0x0F, 0x100B, 0x1010]
    assert tail[0].class_flag == 2 and all(o.subtype_size == 0x12 for o in tail[1:])


@pytest.mark.golden
def test_ctdraws_fast_display_file_strips_the_input_records_too():
    """CtDraw's own .ctF of FxInputs (oracle pages/FxMain/FxInputs) carries its touch
    records the same way: dwFlags 0x12."""
    from ctgedit.format.page import parse_ctg_bytes

    ctdraw = parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/FxInputs", "page.ctF")).page.objects
    assert any(o.type_code == 0x1010 and o.subtype_size == 0x12 for o in ctdraw)


# --------------------------------------------------------------------------- structure
def test_delete_undo_duplicate_keep_the_pairs(tmp_path):
    doc = _open(tmp_path)
    r = _new_rect(doc)
    doc.set_input_commands(r, touch={"CMD": "Beep()"}, keys=[{"KEY_SEQ": "ENTER", "CMD": "A()"}])
    n = len(doc.objects)
    dups = doc.duplicate_objects([r])
    d = dups[0]
    objs = doc.objects
    assert len(objs) == n + 3 and objs[d].class_flag == 2
    hd = G.get_handle(objs[d])
    assert hd != G.get_handle(objs[r])  # a fresh AN ...
    assert [G.get_handle(objs[c]) for c in doc.input_children(d)] == [hd, hd]  # ... shared
    # copy / paste carries the subtree; the pasted owner and its records share a new AN
    pasted = doc.paste_payload(doc.copy_payload([r]))
    objs = doc.objects
    hp = G.get_handle(objs[pasted[0]])
    assert objs[pasted[0]].class_flag == 2 and hp not in (hd, G.get_handle(objs[r]))
    assert [G.get_handle(objs[c]) for c in doc.input_children(pasted[0])] == [hp, hp]
    doc.undo()
    doc.undo()  # paste + duplicate undone
    assert len(doc.objects) == n
    doc.delete_objects([r])  # the owner takes its commands
    assert len(doc.objects) == n - 3
    assert all(
        o.type_code not in IN.INPUT_TCS or IN.parents(doc.objects)[k] is not None
        for k, o in enumerate(doc.objects)
    )
    doc.undo()
    assert doc.input_commands(r)["touch"]["CMD"] == "Beep()"
    assert doc.objects[r].class_flag == 2


def test_deleting_a_group_member_undoes_the_child_count(tmp_path):
    """Undo of a member delete restores the group's child count."""
    doc = _open(tmp_path)
    a = doc.create_object("rectangle", rect=(10, 10, 20, 20))
    b = doc.create_object("rectangle", rect=(30, 30, 40, 40))
    g = doc.group_objects([a, b])
    member = g + 2
    doc.delete_objects([member])
    assert doc.objects[g].class_flag == 1
    doc.undo()
    assert doc.objects[g].class_flag == 2 and len(doc.page.objects[g].members) == 2


def test_ungroup_keeps_input_owners_and_drops_the_groups_commands(tmp_path):
    doc = _open(tmp_path)
    r = _new_rect(doc)
    doc.add_input(r, "touch", {"CMD": "Beep()"})
    assert doc.ungroup_object(r) == []  # not a group
    assert doc.touch_child(r) is not None
    a = doc.create_object("rectangle", rect=(10, 10, 20, 20))
    b = doc.create_object("rectangle", rect=(30, 30, 40, 40))
    g = doc.group_objects([a, b])
    doc.add_input(g, "keyboard", {"KEY_SEQ": "ENTER", "CMD": "A()"})
    n = len(doc.objects)
    doc.ungroup_object(g)
    # the group's keyboard command must not become a PAGE-wide one
    assert len(doc.objects) == n - 2
    assert not [
        k
        for k, o in enumerate(doc.objects)
        if o.type_code in IN.INPUT_TCS and IN.parents(doc.objects)[k] is None
    ]


def test_hollow_rect_in_a_group_with_a_touch_command_stays_hollow(tmp_path):
    """A project's control-button face rule (a fill-less rect grouped with a Touch
    record renders as a gray button) is for genie / library members only; a
    user's own hollow rect in a group with a touch command stays hollow."""
    doc = _open(tmp_path)
    a = doc.create_object("rectangle_outline", rect=(10, 10, 60, 40), colour=0xFF0000)
    b = doc.create_object("text", rect=(12, 12, 50, 30), text="Go")
    g = doc.group_objects([a, b])
    doc.add_input(g, "touch", {"CMD": "Beep()"})
    rect = next(
        doc.page.objects[m]
        for m in doc.page.objects[g].members
        if doc.page.objects[m].raw.get("_type_code") == 0x0F
    )
    assert not rect.style.filled and not rect.raw.get("_button_face")


def test_move_carries_the_input_records(tmp_path):
    doc = _open(tmp_path)
    r = _new_rect(doc)
    t = doc.add_input(r, "touch", {"CMD": "Beep()"})
    doc.move_objects([r], 15, -5)
    a_owner = G.parse_geom_header(doc.objects[r].body_raw).anchor
    gh = G.parse_geom_header(doc.objects[t].body_raw)
    assert gh.anchor == a_owner and gh.rect1[:2] == a_owner
    assert struct.unpack_from("<2h", doc.objects[t].body_raw, len(doc.objects[t].body_raw) - 4) == a_owner


# --------------------------------------------------------------------------- rules
def test_buttons_keep_touch_commands_in_their_own_bag(tmp_path):
    doc = _open(tmp_path)
    b = doc.create_object("button", rect=(10, 10, 80, 40), text="Go")
    n = len(doc.objects)
    doc.set_input_commands(b, touch={"CMD": 'PageDisplay("Menu")', "DOWN_CMD": "X = 1"})
    props = doc.object_properties(b)
    assert props["CMD"] == 'PageDisplay("Menu")' and props["DOWN_CMD"] == "X = 1"
    assert len(doc.objects) == n and doc.touch_child(b) is None
    doc.add_input(b, "keyboard", {"KEY_SEQ": "F5", "CMD": "Y()"})
    assert len(doc.keyboard_children(b)) == 1


def test_limits_and_non_owners(tmp_path):
    doc = _open(tmp_path)
    r = _new_rect(doc)
    with pytest.raises(ValueError, match="253"):
        doc.set_input_commands(r, touch={"CMD": "x" * 254})
    with pytest.raises(ValueError, match="32"):
        doc.set_input_commands(r, touch={"CMD": "A()", "MSG_LOG": "m" * 33})
    with pytest.raises(ValueError, match="key sequence"):
        doc.set_input_commands(r, keys=[{"KEY_SEQ": "F1 " * 12, "CMD": "A()"}])
    t = doc.add_input(r, "touch", {"CMD": "A()"})
    ok, reason = doc.input_owner_status(t)
    assert not ok and reason
    assert len(doc.objects[r : r + 2]) == 2 and doc.touch_child(r) == t


def test_key_sequence_advice():
    from ctgedit.ui.objprops.input import key_sequence_warnings

    assert any("hot key" in w for w in key_sequence_warnings("* F2"))
    assert any("never run" in w for w in key_sequence_warnings("F3", ["F3 F4"]))
    assert key_sequence_warnings("ENTER", ["F2"]) == []


def test_key_menu_offers_the_projects_key_names(tmp_path):
    """The key table beside the page (a copy of Include's keys.DBF with a key of the
    test's own, FX_HOTKEY) gives the menu its names."""
    from ctgedit.format.dbf import Dbf, create_table
    from ctgedit.ui.objprops.input import key_sequence_choices

    with Dbf(FX["install"].file("Include", "keys.DBF"), writable=False) as d:
        fields = [(n, w) for n, _o, w in d.fields]
        rows = [d.decode(d.read(i)) for i in range(d.nrec)]
    rows.append({"NAME": "FX_HOTKEY", "CODE": "KEY_F9", "COMMENT": ""})
    create_table(str(tmp_path / "KEYS.DBF"), fields, rows)
    doc = _open(tmp_path)
    ch = key_sequence_choices(doc)
    assert "ESC" in ch and "F2" in ch and "LBUTTON_CMD_UP" in ch and "### ENTER" in ch
    assert "FX_HOTKEY" in ch


# --------------------------------------------------------------------------- UI
def test_property_sheet_input_tab_is_one_undo_step(tmp_path):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    doc = _open(tmp_path)
    r = _new_rect(doc)
    depth = len(doc._undo)
    dlg = ObjectPropertiesDialog(doc, r)
    inp = dlg._input
    assert inp is not None and dlg._tabs.tabText(dlg._tabs.indexOf(inp)) == "Input"
    assert [inp.tabText(i) for i in range(inp.count())] == ["Touch", "Keyboard Commands"]
    inp.action_checks["CMD"].setChecked(True)
    inp.command_edits["CMD"].setPlainText("Beep()\nX = 1")
    inp.action_checks["DOWN_CMD"].setChecked(True)
    inp.command_edits["DOWN_CMD"].setPlainText("Y = 2")
    inp.log_edits["MSG_LOGDN"].setText("pressed")
    inp.rate.setValue(300)
    inp.add_key_sequence("F2", "Z()")
    inp.add_key_sequence("ENTER")
    inp.same_priv.setChecked(False)
    inp.key_priv.setText("3")
    assert dlg.apply() is True
    info = doc.input_commands(r)
    assert info["touch"] == {
        "ACTION": "KEY_LBUTTON",
        "RPT_RATE": "300",
        "CMD": "Beep()\r\nX = 1",
        "DOWN_CMD": "Y = 2",
        "MSG_LOGDN": "pressed",
    }
    assert info["keys"] == [{"KEY_SEQ": "F2", "CMD": "Z()"}, {"KEY_SEQ": "ENTER", "PRIV": "3"}]
    assert len(doc._undo) == depth + 1
    doc.undo()
    assert doc.input_children(r) == [] and doc.objects[r].class_flag == 0
    # an over-long command keeps the dialog open with a message
    dlg2 = ObjectPropertiesDialog(doc, r)
    dlg2._input.command_edits["CMD"].setPlainText("x" * 300)
    assert "253" in (dlg2._input.validate() or "")


def test_double_click_text_with_touch_opens_its_sheet(tmp_path):
    """FxTouchText owns only its Touch record: a double-click opens the text's property
    sheet."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    w = MainWindow()
    try:
        w.open_document(FX["page"])
        i = _named(w.doc, "FxTouchText")
        w._on_object_activated(i)
        dlg = w._dialogs.get(("objprops", i))
        assert isinstance(dlg, ObjectPropertiesDialog)
        assert dlg._input.command_edits["CMD"].value() == "WinFree();"
        assert not w.act_ungroup.isEnabled()
        dlg.reject()
        w.edit_input(i)
        dlg = w._dialogs.get(("objprops", i))
        assert dlg._tabs.currentWidget() is dlg._input
        dlg.reject()
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()


def test_panel_and_tree_show_the_commands(tmp_path):
    from ctgedit.ui.panels.objects_dock import _row_text
    from ctgedit.ui.panels.properties import PropertyPanel
    from PySide6 import QtWidgets

    _app()
    doc = _open(tmp_path)
    r = _new_rect(doc)
    doc.set_input_commands(
        r, touch={"CMD": "Beep()"}, keys=[{"KEY_SEQ": "ENTER", "CMD": "A()"}, {"KEY_SEQ": "F2", "CMD": "B()"}]
    )
    text = _row_text(r, doc.page.objects[r], doc.page)
    assert "[touch, 2 keys]" in text and "(3)" not in text
    panel = PropertyPanel()
    panel.set_target(doc, [r])
    boxes = {b.title(): b for b in panel.widget().findChildren(QtWidgets.QGroupBox)}
    assert "Input" in boxes and "Group" not in boxes
    edits = boxes["Input"].findChildren(QtWidgets.QLineEdit)
    up = next(e for e in edits if e.text() == "Beep()")
    up.setText("Boop()")
    up.editingFinished.emit()
    assert doc.input_commands(r)["touch"]["CMD"] == "Boop()"
    key_b = next(e for e in edits if e.text() == "B()")
    key_b.setText("")
    key_b.editingFinished.emit()
    # clearing a key's command keeps the key sequence record
    assert [k.get("CMD", "") for k in doc.input_commands(r)["keys"]] == ["A()", ""]
    # Add animation > Touch command on a fresh rect shows the empty rows
    r2 = _new_rect(doc)
    panel.set_target(doc, [r2])
    panel._add_input_command(r2, "@touch")
    boxes = {b.title(): b for b in panel.widget().findChildren(QtWidgets.QGroupBox)}
    assert "Input" in boxes and doc.touch_child(r2) is None


def test_button_sheet_applies_without_touching_the_up_command(tmp_path):
    """A button's Input tab has no Double click action; OK / Apply on a button
    sheet whose Up command is unchanged raises no error."""
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    doc = _open(tmp_path)
    b = doc.create_object("button", rect=(10, 10, 80, 40), text="Go")
    dlg = ObjectPropertiesDialog(doc, b)
    assert "DBLCLK_CMD" not in dlg._input.command_edits
    dlg.apply()  # nothing changed: no error
    inp = dlg._input
    inp.action_checks["REPEAT_CMD"].setChecked(True)
    inp.command_edits["REPEAT_CMD"].setPlainText("Counter = Counter + 1")
    assert dlg.apply() is True
    props = doc.object_properties(b)
    assert props["REPEAT_CMD"] == "Counter = Counter + 1"
    assert doc.touch_child(b) is None  # a button keeps them itself


def _chain(objs, index):
    from ctgedit.format import placement as P

    par = IN.parents(objs)
    out, k = [], index
    while k is not None:
        out.append(P.link_of(objs[k]))
        k = par[k]
    return out


@pytest.mark.golden
def test_input_records_under_transform_carriers_are_ctdraws():
    """Input records under transform carriers match CtDraw."""
    from ctgedit.format import genie_place as GP

    for unit, name in (("touch_on_linked_symbol", "FxLinkedBox"), ("touch_on_symbol_set", "FxOnOffSet")):
        objs = parse_ctg(fxinstall.oracle("draw", unit, "page.ctg")).page.objects
        par = IN.parents(objs)
        (owner,) = [k for k, o in enumerate(objs) if par[k] is None and GP.decode(o).name == name]
        (child,) = _children(objs, owner, IN.TOUCH_TC)
        rec = objs[child]
        vals = dict(IN.values(rec))
        assert vals == {"ACTION": "KEY_LBUTTON", "DOWN_CMD": "Beep()"}, unit
        vals["RPT_RATE"] = ""  # (rate 0: CtDraw writes no RPT_RATE)
        new = factory.make_input_record(
            "touch",
            G.get_handle(objs[owner]),
            G.parse_geom_header(objs[owner].body_raw).anchor,
            vals,
            chain=_chain(objs, owner),
        )
        assert _value_ov(new) == 11
        assert _session_free(new) == _session_free(rec), (unit, child)


def test_touch_on_a_placed_linked_symbol(tmp_path):
    """A placed linked symbol (dwFlags 0x19, no style block: FxSymbols' FxLinkedBox, the
    instance forest of the group-root symbol fxsym.fx_box) can own touch commands (CtDraw
    writes free Touch children under its root: the oracle's draw touch_on_linked_symbol);
    the new record follows its owner through a move and registers the owner's AN."""
    from ctgedit.format import placement as P

    doc = _open(tmp_path, FX["install"].page("FxSymbols"))
    s = _named(doc, "FxLinkedBox")
    po = doc.objects[s]
    assert doc.symbol_link(s)["kind"] == "group"
    assert po.subtype_size == 0x19 and not po.subtype_size & 0x2
    assert doc.input_owner_status(s) == (True, "")
    t = doc.add_input(s, "touch", {"CMD": "Beep()"})
    objs = doc.objects
    ax, ay = G.parse_geom_header(objs[s].body_raw).anchor
    (m1, l1, _o, _n), (m2, l2, _o2, _n2), (m3, l3, _o3, _n3) = P.read_slots(objs[t].body_raw)[0]
    _mt, tot = P.slot3(objs[s])  # the owner's Total
    assert (m1, m2, m3) == (1, 1, 1) and l1 == l2
    assert (l1[4] + tot[4], l1[5] + tot[5]) == (ax, ay) and l3[4:] == (ax, ay)
    assert struct.unpack_from("<2h", objs[t].body_raw, len(objs[t].body_raw) - 4) == (0, 0)
    an = str(G.get_handle(objs[s]) + 1)
    assert [x["CMD"] for x in PR.page_rows(doc.parsed, "FxSymbols")["pgtouch"] if x["AN"] == an] == ["Beep()"]
    doc.move_objects([s], 15, -5)
    objs = doc.objects
    ax2, ay2 = G.parse_geom_header(objs[s].body_raw).anchor
    (_m1, l1b, *_r), _s2, (_m3, l3b, *_r3) = P.read_slots(objs[t].body_raw)[0]
    assert (ax2, ay2) == (ax + 15, ay - 5) and l1b == l1 and l3b[4:] == (ax2, ay2)
    assert G.parse_geom_header(objs[t].body_raw).anchor == (ax2, ay2)


def test_rotated_owner_refuses_a_new_record_with_a_reason(tmp_path):
    """FxMotion's FxSymStretched: a linked symbol stretched by its transform."""
    doc = _open(tmp_path, FX["install"].page("FxMotion"))
    s = _named(doc, "FxSymStretched")
    with pytest.raises(ValueError, match="scaled or rotated"):
        doc.add_input(s, "touch", {"CMD": "Beep()"})
    assert doc.input_children(s) == []


def test_keyboard_command_without_a_key_sequence_is_refused(tmp_path):
    doc = _open(tmp_path)
    r = _new_rect(doc)
    with pytest.raises(ValueError, match="key sequence"):
        doc.set_input_commands(r, keys=[{"KEY_SEQ": "", "CMD": "A()"}])
    with pytest.raises(ValueError, match="key sequence"):
        doc.add_input(r, "keyboard", {"CMD": "A()"})
    assert doc.input_children(r) == []


def test_owner_status_from_the_model_is_the_records_answer(tmp_path):
    """Input owner status from the model matches the records."""
    import glob
    from ctgedit.editing import EditableDocument

    pages = sorted(glob.glob(os.path.join(FX["main"], "*.ctg"))) + sorted(
        glob.glob(os.path.join(FX["install"].project("FxSa"), "*.ctg"))
    )
    checked = 0
    for p in pages:
        doc = EditableDocument.open(p)
        objs = doc.parsed.page.objects
        assert [o.parent for o in doc.page.objects] == IN.parents(objs), p
        for i in range(len(objs)):
            new = doc.input_owner_status(i)
            doc._rerender_pending = True  # (the records' path)
            try:
                assert doc.input_owner_status(i) == new, (p, i)
            finally:
                del doc._rerender_pending
            checked += 1
    assert checked > 1000
    # a structural edit in a batch (the derive waits): the records answer
    doc = _open(tmp_path)
    r = _new_rect(doc)
    with doc.batch("probe"):
        t = doc.add_input(r, "touch", {"CMD": "A()"})
        assert doc.input_owner_status(t)[0] is False
        assert doc.input_owner_status(r) == (True, "")
