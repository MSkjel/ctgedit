"""The ctsim-page/0 export."""

import collections
import json
import os
import shutil
import subprocess
import sys

import fxinstall
import pytest

from ctgedit.io import pgregister as PR
from ctgedit.sim import pagemodel as PM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: the fake install's copy (set by the autouse ``_fx`` fixture)
FX = {}


@pytest.fixture(autouse=True)
def _fx(fx_install):
    FX.clear()
    FX.update(install=fx_install, main=fx_install.project("FxMain"), sa=fx_install.project("FxSa"))
    yield


def _page(name, project="FxMain"):
    return FX["install"].page(name, project)


def _open(path):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(path)


def _named(doc, name):
    found = doc.find_objects_by_name(name)
    assert len(found) == 1, name
    return found[0]


def _copy(tmp_path, project, name, monkeypatch):
    dst = tmp_path / (name + ".ctg")
    shutil.copyfile(os.path.join(project, name + ".ctg"), dst)
    monkeypatch.setenv("CTG_LIB_PATH", project)
    return _open(str(dst))


def _compiled_stand_in(project, page):
    """A <page>.RDB beside the page, newer than it (pagemodel only asks that the compiled
    page exists and is not older than the .ctg: the fake install holds no compiler output)."""
    path = os.path.join(project, page + ".RDB")
    with open(path, "wb") as fh:
        fh.write(b"fx stand-in for a compiled page")
    st = os.stat(os.path.join(project, page + ".ctg"))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    return path


def _roundtrip(doc):
    model = doc.export_page_model()
    text = PM.dumps(model)
    again = PM.loads(text)
    assert PM.check_model(again, doc) == []
    return again


def _by_an(model):
    return {o["an"]: o for o in model["objects"]}


def _compare_rows(project, page, model):
    """{table: (stored, exported, equal)} for the model's tables (pagemodel.compare_rows)."""
    return PM.compare_rows(project, model["page"], model["tables"])


# --------------------------------------------------------------------------- the model
def test_model_shape_and_round_trip():
    """FxGenies (a stand-in .RDB beside it): the model's shape; FxMaster: its control row."""
    _compiled_stand_in(FX["main"], "FxGenies")
    doc = _open(_page("FxGenies"))
    m = _roundtrip(doc)
    assert m["format"] == "ctsim-page/0" and m["page"] == "FxGenies" and m["project"] == "FxMain"
    assert m["source"] == "saved" and m["compiled"]["rdb"] and m["compiled"]["rowsMatch"]
    assert m["compiled"]["current"]
    assert m["size"] == [doc.parsed.page.page_width, doc.parsed.page.page_height]
    assert m["background"] == doc.parsed.attributes.bg_color0
    assert m["control"]["NAME"] == "FxGenies"
    assert set(PR.PAGE_TABLES) <= set(m["tables"]) and m["tables"]["pgdynobj"]
    # every record with a handle but the command records, in record order
    objs = doc.parsed.page.objects
    want = [PM._an(po) for po in objs if po.type_code not in PM.INPUT_TCS]
    assert [o["an"] for o in m["objects"]] == list(dict.fromkeys(a for a in want if a > 0))
    o = m["objects"][0]
    assert {"an", "type", "class", "record", "parent", "rect", "bounds", "pos", "children", "colour"} <= set(
        o
    )
    # a parent's children are the ANs whose parent is it
    by = _by_an(m)
    assert any(x["children"] for x in m["objects"])
    for x in m["objects"]:
        for c in x["children"]:
            assert by[c]["parent"] == x["an"]
    master = _open(_page("FxMaster")).export_page_model(owners=False)
    assert master["control"]["NAME"] == "FxMaster"
    assert master["control"]["NEXT_PAGE"] == "FxGenies"


def test_pg_rows_equal_the_project_rows():
    """The exported rows of FxAnim, FxButtons, FxInputs, FxLegacy and FxMaster equal the rows
    FxMain stores for them (ctgedit's own register wrote those: the rows CtDraw writes are
    the golden test below); FxMaster's page keyboard command is a blank AN row."""
    for page in ("FxAnim", "FxButtons", "FxInputs", "FxLegacy", "FxMaster"):
        doc = _open(_page(page))
        m = doc.export_page_model(owners=False)
        res = _compare_rows(FX["main"], page, m)
        assert any(stored for stored, _e, _q in res.values()), page
        for table, (stored, exported, equal) in res.items():
            assert stored == exported == equal, (page, table, res[table])
    keys = [r for r in m["tables"]["pgkey"] if r["AN"] == ""]
    assert keys and any("FxSet" in r.get("CMD", "") for r in keys)


def _ctdraw_project(tmp_path, project, page):
    """A copy of ``project`` with the tables CtDraw's save left."""
    from ctgedit.format.dbf import Dbf
    from ctgedit.io import pgregister as PR

    unit = "%s/%s" % (project, page)
    fxinstall.oracle("pages", unit, "page.ctg")  # (skips until it is captured)
    rows_rel = fxinstall.oracle_rel("pages", unit, "rows.json")
    listed = {e.get("file", "") for e in fxinstall.oracle_manifest().get("files", ())}
    diffs = fxinstall.oracle_json("pages", unit, "rows.json")["tables"] if rows_rel in listed else {}
    dst = tmp_path / ("ctdraw_" + project)
    shutil.copytree(FX["install"].project(project), str(dst))
    prefix = fxinstall.oracle_rel("pages", unit, "files/%s/" % project)
    for e in fxinstall.oracle_manifest().get("files", ()):
        rel = e.get("file", "")
        if rel.startswith(prefix) and "/" not in rel[len(prefix) :]:
            name = rel[len(prefix) :]
            if name.lower().endswith((".dbf", ".ndx")):
                continue  # (the rows below rebuild them)
            shutil.copyfile(fxinstall.oracle("pages", unit, "files/%s/%s" % (project, name)), str(dst / name))
    head = project.lower() + "/"
    for key, diff in diffs.items():
        if not key.lower().startswith(head) or not key.lower().endswith(".dbf"):
            continue
        path = PR._find_file(str(dst), key[len(head) :])
        assert path is not None, key
        with Dbf(path) as d:
            for c in diff.get("changed", ()):
                row = dict(d.decode(d.read(c["index"])), **{k: v[1] for k, v in c["fields"].items()})
                d.write(c["index"], d.encode(row, c["flag"][1].encode("latin-1")))
            recs = []
            for a in diff.get("added", ()):
                a = dict(a)
                flag = a.pop("_flag", " ")
                recs.append(d.encode({n: a.get(n, "") for n in diff["fields"]}, flag.encode("latin-1")))
            if recs:
                d.append(recs)
    for name in os.listdir(str(dst)):
        if name.lower().endswith(".ndx"):
            os.remove(str(dst / name))
    return str(dst)


@pytest.mark.golden
def test_pg_rows_equal_the_rows_ctdraw_writes(tmp_path):
    """The exported rows of each page equal the rows CtDraw's PageOpen + PageSave of it
    registered (oracle pages/FxMain/<page>). CtDraw registers ActiveX controls with no
    OBJ_NAME (FxLegacy's gauges; test_pgregister.test_activex_rows_match_ctdraw), so the
    activex rows compare with the name blanked."""
    for page in ("FxAnim", "FxButtons", "FxInputs", "FxLegacy", "FxMaster"):
        proj = _ctdraw_project(tmp_path, "FxMain", page)
        m = _open(_page(page)).export_page_model(owners=False)
        if "activex" in m["tables"]:
            m["tables"]["activex"] = fxinstall.as_ctdraw_names_controls(m["tables"]["activex"])
        for table, (stored, exported, equal) in _compare_rows(proj, page, m).items():
            assert stored == exported == equal, (page, table)
        shutil.rmtree(proj)


def test_compare_rows_fast_path_counts_like_decoded_rows(tmp_path):
    """compare_rows matches equal record BYTES first, then decodes the rest: a stored record
    that differs only where decoding cannot see it (trailing white space in a field, the
    PAGE spelling) still counts equal, a changed value does not. FxButtons' 12 pgtouch
    rows."""
    from ctgedit.format.dbf import Dbf

    page = "FxButtons"
    doc = _light_document(_page(page))
    rows = {"pgtouch": PM.page_tables(doc, PM._page_name(doc))["pgtouch"]}
    assert len(rows["pgtouch"]) >= 5
    shutil.copyfile(os.path.join(FX["main"], "pgtouch.DBF"), tmp_path / "pgtouch.DBF")
    n = len(rows["pgtouch"])  # (no NDX beside it: a scan)
    assert PM.compare_rows(str(tmp_path), page, rows)["pgtouch"] == (n, n, n)
    with Dbf(str(tmp_path / "pgtouch.DBF")) as d:
        recs = [i for i, r in d.find("PAGE", page) if r[:1] != b"*"]
        o, ln = d.fmap["CMD"]
        po, pl = d.fmap["PAGE"]
        r0 = bytearray(d.read(recs[0]))
        val = bytes(r0[o : o + ln]).rstrip()
        assert len(val) < ln
        r0[o + len(val)] = 0x09  # a tab where a pad space was
        r0[po : po + pl] = bytes(r0[po : po + pl]).upper()
        d.fh.seek(d._off(recs[0]))
        d.fh.write(bytes(r0))
        r1 = bytearray(d.read(recs[1]))
        r1[o] = ord("#") if r1[o] != ord("#") else ord("!")  # a real difference
        d.fh.seek(d._off(recs[1]))
        d.fh.write(bytes(r1))
    assert PM.compare_rows(str(tmp_path), page, rows)["pgtouch"] == (n, n, n - 1)


def _light_document(path):
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg

    doc = EditableDocument(path=path)
    doc.parsed = parse_ctg(path)
    return doc


def test_long_values_are_cut_like_the_dbf_stores_them():
    """FxAnim's FxAccess given a tool tip longer than the pgdynobj COMMENT field and saved
    (the register stores it cut): the export cuts it the same way and says so."""
    doc = _open(_page("FxAnim"))
    w = PM.field_widths(FX["main"], "pgdynobj")["COMMENT"]
    k = _named(doc, "FxAccess")
    assert doc.set_property(k, "COMMENT", "Fx long tool tip " + "x" * (w + 20))
    doc.save()
    doc = _open(_page("FxAnim"))
    m = doc.export_page_model(owners=False)
    rows = [r for r in m["tables"]["pgdynobj"] if r.get("COMMENT")]
    assert rows and all(len(r["COMMENT"].encode("cp1252")) <= w for r in rows)
    assert any(len(r["COMMENT"].encode("cp1252")) == w for r in rows)
    assert any("COMMENT cut to the %d bytes" % w in n for n in m["notes"])
    res = _compare_rows(FX["main"], "FxAnim", m)
    assert res["pgdynobj"][2] == res["pgdynobj"][0]


def test_drawing_details_decode():
    """A symbol set names its states (FxArrange's FxTripleSet: fx_off, fx_on, fx_fault,
    declared 256, array mode 2), a numeric display (FxAnim) is TXTMODE 3 with string
    section flag 1, scaled genie members (FxSaMaster's panes) carry a
    movement magnitude, a pipe (FxShapes) its vertices."""
    doc = _open(_page("FxArrange"))
    m = _roundtrip(doc)
    an = PM._an(doc.parsed.page.objects[_named(doc, "FxTripleSet")])
    s = _by_an(m)[an]["symbolSet"]
    assert s["names"] == ["fxsym.fx_off", "fxsym.fx_on", "fxsym.fx_fault"]
    assert s["kinds"] == ["linked"] * 3 and s["declared"] == 256
    assert s["states"] == len(s["names"]) == 3 and s["mode"] == 2
    m = _roundtrip(_open(_page("FxAnim")))
    nums = [o for o in m["objects"] if o.get("text", {}).get("mode") == 3]
    assert nums and all(o["text"]["flag"] == 1 for o in nums)
    m = _roundtrip(_open(_page("FxSaMaster", "FxSa")))
    assert any("magnitude" in o for o in m["objects"])
    m = _roundtrip(_open(_page("FxShapes")))
    assert any(o["class"] == "pipe" and len(o.get("vertices") or ()) >= 2 for o in m["objects"])


#: eight fxmany.ctl states for the spinner-like set (the fixture library's first eight)
SPIN_STATES = (
    "fx_pump_small",
    "fx_pump_large",
    "fx_pump_side",
    "fx_pmp_mark",
    "fx_primp",
    "fx_tank_tall",
    "fx_tank_small",
    "fx_motor",
)


def test_a_set_with_an_empty_first_state_names_the_rest():
    """The busy spinner form, drawn here on FxShapes of the fake install's copy: an animated
    symbol set (array mode 3) whose state 0 is empty and whose states 1 to 8 link the first
    eight fxmany.ctl symbols. Its export: names[0] None, kinds[0] 'empty', the eight names,
    declared 256, nine states, mode 3."""
    from ctgedit.format import symbolset as SS

    doc = _open(_page("FxShapes"))
    i = doc.create_symbol_set(
        (600, 500), kind=SS.ANIMATED, exprs=["FX_MODE"], states=[None] + [("fxmany", e) for e in SPIN_STATES]
    )
    m = _roundtrip(doc)
    s = _by_an(m)[PM._an(doc.parsed.page.objects[i])]["symbolSet"]
    assert s["names"][0] is None and s["kinds"][0] == "empty" and s["declared"] == 256
    assert s["names"][1:] == ["fxmany.%s" % e for e in SPIN_STATES]
    assert s["kinds"][1:] == ["linked"] * 8
    assert s["states"] == len(s["names"]) == 9 and s["mode"] == 3


def test_static_text_exports_its_font_and_anchor():
    # the display client re-measures a static caption it shows translated from its own font
    # and anchor (the engine's CicodeExtents.StaticTexts): FxAnim's text of '@()' captions,
    # made bold here
    doc = _open(_page("FxAnim"))
    m = doc.export_page_model(owners=False)
    cap = "@(Fx Start) @(Fx Stop) @(Fx Mode)"
    rec = next(o["record"] for o in m["objects"] if (o.get("text") or {}).get("caption") == cap)
    assert doc.set_font(rec, bold=True)
    m = _roundtrip(doc)
    texts = [o for o in m["objects"] if (o.get("text") or {}).get("caption") == cap]
    assert texts, "the label is a static text with a caption"
    f = texts[0]["text"]["font"]
    assert f["face"] and f["points"] > 0 and f["bold"]
    assert f["justify"] in (0, 1, 2) and f["orient"] == 1
    assert f["anchor"] == texts[0]["pos"]
    r = texts[0]["rect"]
    assert r[0] <= f["anchor"][0] <= r[2] and r[1] <= f["anchor"][1] <= r[3]


def test_trend_pens_of_a_trend():
    """FxLegacy's FxTrend: its own eight pens and 301 samples."""
    m = _roundtrip(_open(_page("FxLegacy")))
    tr = [o["trend"] for o in m["objects"] if "trend" in o]
    assert tr and all(t["from"] == "own" and len(t["pens"]) == 8 for t in tr)
    assert {t["samples"] for t in tr} == {301}


def _twin_host(fx_page):
    """A genie library made in the fake install for twin tests."""
    from ctgedit.editing import EditableDocument, ElementDocument

    main = FX["main"]
    lib = os.path.join(main, "fx_twin.ctm")
    ed = ElementDocument.new_element(lib, "fx_twin", "genie", canvas=(400, 300))
    t = ed.create_object("trend", (10, 10, 310, 160))
    assert ed.set_trend_properties(t, pens=[("%%Tag%%_%d" % k, None) for k in range(1, 9)])
    p = ed.create_object("polygon", None, [(10, 200), (70, 200), (40, 250)])
    assert ed.ensure_property(p, "VERTEX_VERTEXP1", "%Tag%")
    ed.save()
    page = os.path.join(main, "FxTwinHost.ctg")
    doc = EditableDocument.new_blank(fx_page("FxShapes"), page, register=False)
    assert (
        doc.place_library_element("fx_twin", "fx_twin", "ctm", (100, 100), params={"Tag": "FX_LEVEL1"})
        is not None
    )
    q = doc.create_object("polygon", None, [(500, 400), (560, 400), (530, 450)])
    assert doc.ensure_property(q, "VERTEX_VERTEXP1", "FX_LEVEL2")
    doc.save()
    return _open(page), lib


def test_trend_pens_come_from_the_element_twin(fx_page):
    """An art-stripped trend member (:func:`_twin_host`) takes its eight pens and its samples
    from the element twin (t['from'] == 'element'; the twin's sample count, 301 here)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import legacy

    doc, lib = _twin_host(fx_page)
    m = _roundtrip(doc)
    tr = [o["trend"] for o in m["objects"] if "trend" in o]
    assert tr and all(t["from"] == "element" and len(t["pens"]) == 8 for t in tr)
    twin = ElementDocument.open_element(lib, "fx_twin")
    own = [legacy.trend_info(po) for po in twin.parsed.page.objects if po.type_code < 0x2000]
    samples = {x["samples"] for x in own if x is not None}
    assert samples == {301}
    assert {t["samples"] for t in tr} == samples


def test_metadata_and_vertices_on_a_page():
    """FxAnim: metadata pairs, and the free polygon of its vertex animation carries its
    vertex list."""
    doc = _open(_page("FxAnim"))
    m = _roundtrip(doc)
    md = [o for o in m["objects"] if o.get("metadata")]
    assert md and all(all(len(p) == 2 for p in o["metadata"]) for o in md)
    an = {int(r["AN"]) for r in m["tables"]["pgvertex"]}
    assert an
    by = _by_an(m)
    v = {a: by[a].get("vertices") for a in an}
    assert all(isinstance(x, list) and x for x in v.values())


def test_art_stripped_members_carry_their_twins_vertex_count(fx_page):
    """The pgvertex rows of :func:`_twin_host`: the free polygon carries its vertex list, the
    art-stripped genie member its element twin's vertex count (an int, 3: the .ctF bakes the
    twin's vertex array)."""
    doc, _lib = _twin_host(fx_page)
    m = _roundtrip(doc)
    an = {int(r["AN"]) for r in m["tables"]["pgvertex"]}
    by = _by_an(m)
    v = {a: by[a].get("vertices") for a in an}
    assert len(v) == 2
    assert all(isinstance(x, int) and x > 0 or isinstance(x, list) and x for x in v.values())
    assert [x for x in v.values() if isinstance(x, int)] == [3]
    assert [len(x) for x in v.values() if isinstance(x, list)] == [3]


@pytest.mark.golden
def test_sections_equal_the_runtime_ctf(tmp_path):
    """Sections equal the runtime .ctF."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import legacy
    from ctgedit.format import symbolset as SS
    from ctgedit.format.page import parse_ctg

    n = collections.Counter()
    for project, page in (
        ("FxMain", "FxAnim"),
        ("FxMain", "FxSymbols"),
        ("FxMain", "FxShapes"),
        ("FxMain", "FxLegacy"),
        ("FxMain", "FxGenies"),
        ("FxSa", "FxSaMaster"),
    ):
        unit = "%s/%s" % (project, page)
        g = os.path.join(FX["install"].project(project), page + ".ctg")
        shutil.copyfile(fxinstall.oracle("pages", unit, "page.ctg"), g)  # CtDraw's save
        doc = EditableDocument(path=g)
        doc.parsed = parse_ctg(g)
        m = PM.export_document(doc, tables=False)
        b = parse_ctg(fxinstall.oracle("pages", unit, "page.ctF")).page.objects
        by = {}
        for po in b:
            if po.type_code not in PM.INPUT_TCS:
                by.setdefault((PM._an(po), po.type_code), po)
        for o in m["objects"]:
            q = by.get((o["an"], o["type"]))
            if q is None:
                continue  # baked into the background record
            assert o.get("colour") == PM.decode_colour(q), (page, o["an"])
            n["colour"] += 1
            if o["type"] == PM.TEXT_TC and PM.decode_text(q) is not None:
                t = PM.decode_text(q)
                assert {k: o["text"][k] for k in t} == t, (page, o["an"])
                n["text"] += 1
            if o["type"] in PM.COUNTED_NODE_TCS and isinstance(o.get("vertices"), int):
                assert o["vertices"] == len(PM._nodes(q)), (page, o["an"])
                n["count"] += 1
            if o["type"] == PM.TREND_TC and legacy.trend_info(q) is not None:
                ti = legacy.trend_info(q)
                assert o["trend"]["pens"] == [list(x) for x in ti["pens"]]
                assert o["trend"]["samples"] == ti["samples"]
                n["trend"] += 1
            if o["type"] == PM.SET_TC and SS.class_section(q) is not None:
                assert o["symbolSet"]["declared"] == SS.class_section(q)["declared"]
                n["set"] += 1
    assert n["colour"] > 50 and n["text"] and n["set"] and n["count"] and n["trend"], dict(n)
    assert n["count"] >= 8, dict(n)  # (FxGenies' members)


def test_check_model_finds_a_changed_value():
    doc = _open(_page("FxAnim"))
    base = json.loads(PM.dumps(doc.export_page_model()))
    assert PM.check_model(base, doc) == []
    for mutate in (
        lambda o: o["rect"].__setitem__(0, o["rect"][0] + 1),
        lambda o: o["colour"]["state0"].__setitem__(0, 0x123456),
        lambda o: o.__setitem__("parent", 99999),
    ):
        m = json.loads(json.dumps(base))
        mutate(m["objects"][-1])
        assert PM.check_model(m, doc), "a mutation went unnoticed"
    m = json.loads(json.dumps(base))
    m["tables"]["pgdynobj"][0]["AN"] = "0"
    assert "table pgdynobj" in PM.check_model(m, doc)


# --------------------------------------------------------------------------- unsaved edits
def test_unsaved_move_shows_in_the_export(tmp_path, monkeypatch):
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    doc = _copy(tmp_path, FX["main"], "FxGenies", monkeypatch)
    before = _by_an(doc.export_page_model())
    from ctgedit.format.symbolset import subtree_end

    objs = doc.parsed.page.objects

    def members(r):
        return [
            PM._an(objs[i]) for i in range(r, subtree_end(objs, r)) if objs[i].type_code not in PM.INPUT_TCS
        ]

    root = [r for r in doc.page.roots() if len(members(r)) > 3][-1]  # a group / genie
    ans = members(root)
    doc.move_objects([root], 10, 5)
    m = _roundtrip(doc)
    assert m["source"] == "unsaved" and not m["compiled"].get("current")
    after = _by_an(m)
    for a in ans:
        l, t, r, b = before[a]["rect"]
        assert after[a]["rect"] == [l + 10, t + 5, r + 10, b + 5]
        assert after[a]["pos"] == [before[a]["pos"][0] + 10, before[a]["pos"][1] + 5]


def test_unsaved_colour_animation_shows_in_the_export(tmp_path, monkeypatch):
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    # an existing On / Off fill colour: a rectangle given one and saved on a copy of
    # FxGenies (FxAnim's FxColA has that shape, but FxAnim's page header holds bytes in its
    # area field that a full derive reads back otherwise: a shared request)
    doc = _copy(tmp_path, FX["main"], "FxGenies", monkeypatch)
    r = doc.create_object("rectangle", rect=(700, 20, 760, 50), colour=0x00808080)
    assert doc.set_fill_keys(r, [("COLMODE", "0"), ("COL_A", "FX_PUMP2")])
    doc.save()
    doc = _open(doc.path)
    # a new ON colour and condition for it
    target = len(doc.parsed.page.objects) - 1
    po = doc.parsed.page.objects[target]
    d = {k.upper(): v for k, v in PR._value_bag(po)}
    assert po.type_code == 0x0F and "COL_A" in d and d.get("COLMODE", "0") == "0"
    assert po.subtype_size & 0x2
    an = PM._an(doc.parsed.page.objects[target])
    assert doc.set_fill_keys(target, [("COL_A", "FX_PUMP1"), ("COL_MAXON", "0x00123456,0x00654321")])
    # a new rectangle with an On / Off colour animation
    new = doc.create_object("rectangle", rect=(20, 20, 60, 50), colour=0x00336699)
    assert doc.set_fill_keys(new, [("COL_A", "FX_LEVEL1 > 50"), ("COL_MAXON", "0x00ff0000,0x00ff0000")])
    new_an = PM._an(doc.parsed.page.objects[new])
    m = _roundtrip(doc)
    by = _by_an(m)
    assert by[an]["colour"]["maxon"] == [0x123456, 0x654321]
    assert by[new_an]["colour"]["maxon"] == [0xFF0000, 0xFF0000]
    assert by[new_an]["colour"]["state0"][0] == 0x336699
    rows = {r["AN"]: r for r in m["tables"]["pgdynobj"]}
    assert rows[str(an)]["COL_A"] == "FX_PUMP1"
    assert rows[str(new_an)]["COL_A"] == "FX_LEVEL1 > 50"
    assert rows[str(new_an)]["TYPE"] == "0x000F" and rows[str(new_an)]["GROUP"] == ""


def test_a_record_that_does_not_decode_keeps_its_object(monkeypatch):
    """One odd record never drops its AN: the object stays (with what needs no section
    decode) so the AN list and every parent's 'children' stay whole; a page in an old form
    (FxOld9, word2 9) round-trips."""
    doc = _open(_page("FxGenies"))
    full = doc.export_page_model(owners=False, tables=False)
    victim = full["objects"][len(full["objects"]) // 2]
    real = PM.object_summary

    def boom(objs, i, *a, **k):
        if i == victim["record"]:
            raise ValueError("odd record")
        return real(objs, i, *a, **k)

    monkeypatch.setattr(PM, "object_summary", boom)
    m = doc.export_page_model(owners=False, tables=False)
    assert [o["an"] for o in m["objects"]] == [o["an"] for o in full["objects"]]
    got = _by_an(m)[victim["an"]]
    assert {k: got[k] for k in ("type", "record", "parent", "children", "flags")} == {
        k: victim[k] for k in ("type", "record", "parent", "children", "flags")
    }
    assert any("odd record" in n for n in m["notes"])
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg

    old = _page("FxOld9")
    d = EditableDocument(path=old)
    d.parsed = parse_ctg(old)
    monkeypatch.setattr(PM, "object_summary", real)
    assert PM.check_model(PM.loads(PM.dumps(PM.export_document(d))), d) == []


def test_file_rewritten_elsewhere_is_not_saved(tmp_path, monkeypatch):
    """A clean document whose file something else rewrote since it was read is not 'saved':
    its in-memory records are not the file (the engine must not trust the .RDB for it)."""
    doc = _copy(tmp_path, FX["main"], "FxAnim", monkeypatch)
    assert doc.export_page_model(owners=False)["source"] == "saved"
    st = os.stat(doc.path)
    os.utime(doc.path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    m = doc.export_page_model(owners=False)
    assert m["source"] == "unsaved" and not m["compiled"].get("current")


def test_export_reads_nothing_it_writes(monkeypatch):
    """The export of a page in a project leaves every project file as it was (FxSa's
    FxSaMaster, a stand-in .RDB beside it so the row comparison runs)."""
    sa = FX["sa"]
    _compiled_stand_in(sa, "FxSaMaster")
    files = [
        os.path.join(sa, f)
        for f in os.listdir(sa)
        if f.lower().endswith((".dbf", ".ndx", ".ctg", ".ctf", ".cit", ".rdb"))
    ]
    stamps = {p: os.stat(p).st_mtime_ns for p in files}
    doc = _open(_page("FxSaMaster", "FxSa"))
    # no file is even OPENED for writing
    import builtins

    real = builtins.open
    writes = []

    def spy(f, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            writes.append((str(f), mode))
        return real(f, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    m = doc.export_page_model()
    monkeypatch.setattr(builtins, "open", real)
    assert m["compiled"].get("rowsMatch") is not None  # the comparison ran
    assert writes == []
    assert {p: os.stat(p).st_mtime_ns for p in files} == stamps


# --------------------------------------------------------------------------- tool + boundary
def test_tool_writes_json_and_checks():
    env = dict(os.environ, PYTHONPATH=ROOT, CTG_NO_CTF_PRECHECK="1")
    page = _page("FxAnim")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "sim_page_model.py"), page, "--check"],
        capture_output=True,
        env=env,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    m = json.loads(r.stdout.decode("ascii"))
    assert m["format"] == "ctsim-page/0" and m["page"] == "FxAnim" and m["objects"]


def test_pagemodel_imports_no_qt():
    code = (
        "import sys; import ctgedit.sim.pagemodel; "
        "sys.exit(1 if any(m.startswith('PySide6') for m in sys.modules) else 0)"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], env=dict(os.environ, PYTHONPATH=ROOT), capture_output=True, timeout=120
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
