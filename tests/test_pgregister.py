"""The page register."""

import os
import random
import shutil
import struct

import pytest

import fxinstall
from ctgedit.format import genie_place as GP, writer as W
from ctgedit.format.dbf import Dbf, Ndx, create_ndx
from ctgedit.format.page import parse_ctg
from ctgedit.io import pgregister as PR

#: the genie page (dynamic members, buttons, keyboard and touch rows)
WTP = "FxGenies"
#: the V3 / V4 page (advisory, trend, keyboard, touch, the two ActiveX gauges)
METERS = "FxLegacy"


@pytest.fixture
def SA(fx_project):
    """FxMain of the fake install copy."""
    return fx_project("FxMain")


def _norm(dbf, row):
    """A generated row as the stored record would read back."""
    return dbf.decode(dbf.encode(row))


def _stored(project, table, page):
    d = Dbf(PR._find_file(project, table + ".DBF"), writable=False)
    n = PR._open_ndx(project, table)
    try:
        rows = [d.decode(r) for _i, r in PR._page_records(d, n, "PAGE", page) if r[:1] != b"*"]
        return d, rows
    finally:
        if n is not None:
            n.close()


def _compare(project, page, tables, ctdraw=False, ctg=None):
    """The rows the register generates for ``page`` (from ``ctg``, else the project's own
    .ctg) against the stored rows of each table: the project's own, or with ``ctdraw`` the
    rows CtDraw's save of the page left (golden)."""
    gen = PR.page_rows(ctg or os.path.join(project, page + ".ctg"), page)
    for t in tables:
        d, have = _stored(project, t, page)
        try:
            if ctdraw:
                have = _ctdraw_rows(project, page, t)
            want = [_norm(d, r) for r in gen[t]]
            have = [r for r in have if r.get("AN", "x")]
            assert sorted(map(repr, want)) == sorted(map(repr, have)), t
        finally:
            d.close()
    return gen


def _ctdraw_rows(project, page, table):
    """The live rows of ``page`` in ``table`` after CtDraw's PageOpen + PageSave of it: the
    fixture's table (``project`` is the fake install copy of FxMain) with the oracle's
    rows.json applied (its rows changed in place, then the rows appended)."""
    diffs = fxinstall.oracle_json("pages", "FxMain/" + page, "rows.json")["tables"]
    diff = next((v for k, v in diffs.items() if k.lower() == "fxmain/%s.dbf" % table), None)
    with Dbf(PR._find_file(project, table + ".DBF"), writable=False) as d:
        base = [(d.read(i)[:1].decode("latin-1"), d.decode(d.read(i))) for i in range(d.nrec)]
    if diff:
        for c in diff["changed"]:
            _f, r = base[c["index"]]
            r = dict(r, **{k: v[1] for k, v in c["fields"].items()})
            base[c["index"]] = (c["flag"][1], r)
        for a in diff["added"]:
            a = dict(a)
            flag = a.pop("_flag", " ")
            base.append((flag, {n: a.get(n, "") for n in diff["fields"]}))
    return [r for f, r in base if f != "*" and r.get("PAGE", "").strip().lower() == page.lower()]


def _ctdraw_page(page):
    """CtDraw's save of FxMain's ``page`` (the oracle's page.ctg)."""
    return fxinstall.oracle("pages", "FxMain/" + page, "page.ctg")


def _an_of(path, name):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(path)
    (i,) = doc.find_objects_by_name(name)
    return str(doc.page.objects[i].an)


# --------------------------------------------------------------------------- rows
def test_genie_page_rows_match_the_stored_rows(SA):
    gen = _compare(SA, WTP, ("pgdynobj", "pgalmind", "pgbutton", "pgkey", "pgtouch"))
    assert gen["pgdynobj"] and gen["pgkey"] and gen["pgtouch"]


@pytest.mark.golden
def test_genie_page_rows_match_ctdraw(SA):
    _compare(
        SA, WTP, ("pgdynobj", "pgalmind", "pgbutton", "pgkey", "pgtouch"), ctdraw=True, ctg=_ctdraw_page(WTP)
    )


def test_number_displays_register_as_type_5_txtmode_3(SA):
    """The page's top-level number displays register as TYPE 0x0005 / TXTMODE 3, no group
    (FxShapes' FxNumber)."""
    gen = PR.page_rows(os.path.join(SA, "FxShapes.ctg"), "FxShapes")
    an = _an_of(os.path.join(SA, "FxShapes.ctg"), "FxNumber")
    assert [
        r
        for r in gen["pgdynobj"]
        if r["AN"] == an and r.get("TYPE") == "0x0005" and r.get("TXTMODE") == "3" and not r.get("GROUP")
    ]


def test_alarm_indicator_rows_follow_ai_on(SA):
    """An alarm-indicator row per record whose own AI_ON is 1 (status lights): FxAnim's
    FxAlarmGroup and FxAlarmGroup2."""
    path = os.path.join(SA, "FxAnim.ctg")
    pc = parse_ctg(path)
    ai = {
        str(PR._an(o))
        for o in pc.page.objects
        if {k.upper(): v for k, v in (W.value_bag(o) or [])}.get("AI_ON", "").strip() == "1"
    }
    assert ai >= {_an_of(path, "FxAlarmGroup"), _an_of(path, "FxAlarmGroup2")}, ai
    gen = _compare(SA, "FxAnim", ("pgalmind",))
    assert {r["AN"] for r in gen["pgalmind"]} == ai


@pytest.mark.parametrize(
    "page,tables",
    [
        (
            METERS,
            (
                "pgdynobj",
                "pgtouch",
                "pgkey",
                "pgadv",
                "pgtrend",
                "pgansym",
                "pgbar",
                "pgcol",
                "pgslider",
                "pgstr",
                "pgtext",
                "pgbutton",
            ),
        ),
        ("FxAnim", ("pgdynobj", "pgvertex", "pgalmind", "pgbutton")),
        ("FxButtons", ("pgdynobj", "pgbutton", "pgkey", "pgtouch")),
    ],
)
def test_rows_match_the_stored_rows(SA, page, tables):
    _compare(SA, page, tables)


@pytest.mark.golden
@pytest.mark.parametrize(
    "page,tables",
    [
        (
            METERS,
            (
                "pgdynobj",
                "pgtouch",
                "pgkey",
                "pgadv",
                "pgtrend",
                "pgansym",
                "pgbar",
                "pgcol",
                "pgslider",
                "pgstr",
                "pgtext",
                "pgbutton",
            ),
        ),
        ("FxAnim", ("pgdynobj", "pgvertex", "pgalmind", "pgbutton")),
        ("FxButtons", ("pgdynobj", "pgbutton", "pgkey", "pgtouch")),
        ("FxInputs", ("pgdynobj", "pgbutton", "pgkey", "pgtouch")),
        ("FxSymbols", ("pgdynobj", "pgtouch")),
    ],
)
def test_rows_match_ctdraw(SA, page, tables):
    _compare(SA, page, tables, ctdraw=True, ctg=_ctdraw_page(page))


def _ctdraw_txtmode(project, page):
    """{AN: TXTMODE} of ``page``'s pgdynobj rows after CtDraw's PageOpen + PageSave."""
    return {r["AN"].strip(): r.get("TXTMODE", "").strip() for r in _ctdraw_rows(project, page, "pgdynobj")}


def _ours_txtmode(page):
    """{AN: TXTMODE} of the rows ctgedit's register makes from CtDraw's save of ``page``
    ('' where the row has none)."""
    rows = PR.page_rows(_ctdraw_page(page), page)["pgdynobj"]
    return {r["AN"]: r.get("TXTMODE", "") for r in rows}


def _an_on(page, name):
    """The AN of the record named ``name`` on CtDraw's save of ``page``."""
    for o in parse_ctg(_ctdraw_page(page)).page.objects:
        try:
            if GP.decode(o).name == name:
                return str(PR._an(o))
        except Exception:  # noqa: BLE001  # swallow-ok: a record that does not decode has no name
            continue
    raise KeyError(name)


@pytest.mark.golden
def test_txtmode_zero_after_colmode_is_not_stored(SA):
    """TXTMODE 0 after COLMODE isn't stored."""
    assert PR._txtmode_after_colmode([("COLMODE", "2"), ("TXTMODE", "0")])
    assert not PR._txtmode_after_colmode([("TXTMODE", "0"), ("COLMODE", "2")])
    assert not PR._txtmode_after_colmode([("TXTMODE", "0")])
    from ctgedit.io.project import Project

    after = None
    for page in [r["NAME"] for r in Project(SA).page_rows()]:
        path = os.path.join(SA, page + ".ctg")
        rows = {r["AN"]: r for r in PR.page_rows(path, page)["pgdynobj"]}
        for o in parse_ctg(path).page.objects:
            bag = [(k.upper(), v) for k, v in (W.value_bag(o) or [])]
            keys = [k for k, _v in bag]
            an = str(PR._an(o))
            if (
                "TXTMODE" in keys
                and "COLMODE" in keys
                and dict(bag)["TXTMODE"] == "0"
                and an in rows
                and keys.index("TXTMODE") > keys.index("COLMODE")
            ):
                after = after or (page, rows[an])
    assert after is not None and "TXTMODE" not in after[1], after
    theirs, ours = _ctdraw_txtmode(SA, "FxAnim"), _ours_txtmode("FxAnim")
    for name, colmode_rule in (("FxColFirst", True), ("FxTxtFirst", False)):
        an = _an_on("FxAnim", name)
        o = next(o for o in parse_ctg(_ctdraw_page("FxAnim")).page.objects if str(PR._an(o)) == an)
        bag = [(k.upper(), v) for k, v in (W.value_bag(o) or [])]
        assert dict(bag)["TXTMODE"] == "0", name
        assert PR._txtmode_after_colmode(bag) is colmode_rule, name
        assert theirs[an] == ours[an] == "", name


@pytest.mark.golden
def test_txtmode_zero_after_txt_off(SA):
    """TXTMODE 0 after TXT_OFF."""
    for page, names in (
        ("FxAnim", ("FxOnOff", "FxTxtFirst")),
        ("FxTextKinds", ("FxTxtOffLabel", "FxBullets")),
    ):
        theirs, ours = _ctdraw_txtmode(SA, page), _ours_txtmode(page)
        for name in names:
            an = _an_on(page, name)
            assert theirs[an] == ours[an] == "", (page, name)
    theirs, ours = _ctdraw_txtmode(SA, "FxParts"), _ours_txtmode("FxParts")
    kept = []
    for o in parse_ctg(_ctdraw_page("FxParts")).page.objects:
        if o.type_code != 0x05:
            continue
        r = GP.decode(o)
        if len(r.entries) != 2 or len(r.entries[0]) < 3:
            continue
        v = [k2.upper() for k2, _x in r.entries[0][1].pairs]
        t = [k2.upper() for k2, _x in r.entries[0][2].pairs]
        if (
            dict(r.entries[0][1].pairs).get("TXTMODE") == "0"
            and not PR._off_before_mode(v)
            and PR._off_before_mode(t)
        ):
            an = str(PR._an(o))
            kept.append((theirs[an], ours[an]))
    assert kept and set(kept) == {("0", "0")}, kept
    # FxOnOff without its TXT_A and TXT_ON: the corpus' free text, which keeps '0'
    from ctgedit.editing import EditableDocument

    on_off = EditableDocument.open(os.path.join(SA, "FxAnim.ctg"))
    (k,) = on_off.find_objects_by_name("FxOnOff")
    r = GP.decode(on_off.parsed.page.objects[k])
    pairs = r.entries[0][1].pairs
    assert PR._txtmode_dropped(GP.to_po(GP.encode(r)), pairs)
    plain = [p for p in pairs if p[0].upper() not in ("TXT_A", "TXT_ON")]
    assert PR._off_before_mode([k2.upper() for k2, _v in plain])
    r.entries[0][1] = GP.Bag(plain, r.entries[0][1].ov)
    assert not PR._txtmode_dropped(GP.to_po(GP.encode(r)), plain)


def test_a_vertex_with_an_empty_expression_gets_no_pgvertex_row(SA):
    """CtDraw's register writes a pgvertex row only for a vertex whose VERTEX_VERTEXP<n> is
       not empty (its page load then takes the other vertices' keys out of the
    bag). FxNoAnNames' FxArrow has expressions on vertices 0 and 2;
       with the expression of vertex 2 emptied (by the test) only vertex 0 has a row."""
    p = parse_ctg(os.path.join(SA, "FxNoAnNames.ctg"))
    objs = p.page.objects
    (k,) = [i for i, o in enumerate(objs) if GP.decode(o).name == "FxArrow"]

    def rows():
        return [
            (r["INDEX"], r["EXPR"])
            for r in PR.page_rows(p, "FxNoAnNames")["pgvertex"]
            if r["AN"] == str(PR._an(objs[k]))
        ]

    assert rows() == [("0", "FX_LEVEL1"), ("2", "FX_LEVEL2")]
    r = GP.decode(objs[k])
    r.entries[0][1] = GP.Bag(
        [(n, "" if n == "VERTEX_VERTEXP2" else v) for n, v in r.entries[0][1].pairs], r.entries[0][1].ov
    )
    objs[k] = GP.to_po(GP.encode(r))
    assert GP.decode(objs[k]).entries[0][1].get("VERTEX_VERTEXP2") == ""
    assert rows() == [("0", "FX_LEVEL1")]


def test_group_member_rows_carry_parent_an(SA):
    """FxShapes' groups hold dynamic members (FxGroupA, FxGroupB, FxOuterGroup)."""
    gen = PR.page_rows(os.path.join(SA, "FxShapes.ctg"), "FxShapes")
    grouped = [r for r in gen["pgdynobj"] if r["GROUP"]]
    ans = {r["AN"] for r in gen["pgdynobj"]}
    assert grouped, "FxShapes has dynamic group members"
    objs, par = PR._records(parse_ctg(os.path.join(SA, "FxShapes.ctg")))
    all_ans = {str(PR._an(o)) for o in objs}
    assert all(r["GROUP"] in all_ans for r in grouped) and ans


def _gauges(SA):
    path = os.path.join(SA, METERS + ".ctg")
    return [(_an_of(path, "FxGaugeA"), "FxGaugeA"), (_an_of(path, "FxGaugeB"), "FxGaugeB")]


def test_activex_rows_match_the_stored_rows(SA):
    page = METERS
    rows = PR.activex_rows(os.path.join(SA, page + ".ctg"), page)
    assert [(r["AN"], r["OBJ_NAME"]) for r in rows] == _gauges(SA)
    d, have = _stored(SA, "activex", page)
    d.close()
    for r in rows:
        assert {k: r[k] for k in ("AN", "OBJ_NAME", "OBJ_GUID")} in [
            {k: h[k] for k in ("AN", "OBJ_NAME", "OBJ_GUID")} for h in have
        ]


@pytest.mark.golden
@pytest.mark.parametrize("page", [METERS, "FxActiveX"])
def test_activex_rows_match_ctdraw(SA, page):
    """ActiveX rows match CtDraw's."""
    from ctgedit.format import records as R

    rows = PR.activex_rows(_ctdraw_page(page), page)
    if page == METERS:
        assert [(r["AN"], r["OBJ_NAME"]) for r in rows] == _gauges(SA)
    made_up = sum(r["OBJ_GUID"] == R.FX_CONTROL_CLSID for r in rows)
    boxes = fxinstall.oracle_dialogs("pages", "FxMain/" + page)
    assert sum(fxinstall.NOT_A_CONTROL in b for b in boxes) == made_up
    keys = ("AN", "OBJ_NAME", "OBJ_GUID")
    have = sorted(tuple(h[k] for k in keys) for h in _ctdraw_rows(SA, page, "activex"))
    assert sorted(tuple(r[k] for k in keys) for r in fxinstall.as_ctdraw_names_controls(rows)) == have


def test_genie_member_user_marker_is_not_registered():
    """Live: a genie member's Access fields set in CtDraw are
    stored 'value<User>'; CtDraw's register writes the value alone
    (pgdynobj COMMENT 'tip two', PRIV '2', DEVICE 'AlarmDisk')."""
    bag = [
        ("COMMENT", "tip two<User>"),
        ("PRIV", "2<User>"),
        ("DEVICE", "AlarmDisk<User>"),
        ("DESC", "d<User>"),
        ("AREA", "3<User>"),
        ("VISIBLE", "X<User>"),
    ]
    got = PR._fields_from(bag, ["COMMENT", "PRIV", "DEVICE", "DESC", "AREA", "VISIBLE"])
    assert got == {
        "COMMENT": "tip two",
        "PRIV": "2",
        "DEVICE": "AlarmDisk",
        "DESC": "d",
        "AREA": "3",
        "VISIBLE": "X<User>",
    }


def test_natives_follow_ctdraw_label_parse():
    assert PR.natives("@(Start) x @(Clamped ^(PID^)) @(a,b) @(Start)") == ["Start", "Clamped (PID)", "a"]
    assert PR.natives("@(unterminated") == []
    assert PR.natives("plain") == []


def test_lang_strings_are_in_pglang(SA):
    """FxAnim's texts and title carry the labels @(Fx Start), @(Fx Stop) and @(Fx Mode)."""
    d = Dbf(os.path.join(SA, "pglang.DBF"), writable=False)
    have = {d.decode(d.read(i))["NATIVE"].lower() for i in range(d.nrec)}
    d.close()
    got = PR.lang_strings(os.path.join(SA, "FxAnim.ctg"))
    assert got == ["Fx Start", "Fx Stop", "Fx Mode"] and all(x.lower() in have for x in got)


# --------------------------------------------------------------------------- NDX
def test_ndx_insert_keeps_a_valid_btree(tmp_path):
    p = str(tmp_path / "t.NDX")
    create_ndx(p, "PAGE", 64)
    rnd = random.Random(7)
    words = ["Alpha", "alpha", "ZZ_top", "Zeta", "_under", "Mixer", "mixer_2", "B", "b"]
    keys = []
    for rec in range(1, 600):
        k = rnd.choice(words) + ("" if rnd.random() < 0.5 else str(rnd.randint(0, 40)))
        keys.append((k, rec))
    with Ndx(p) as n:
        for k, rec in keys:
            n.insert(k, rec)
        ents = n.entries()
        assert len(ents) == len(keys)
        assert [(k.rstrip().lower(), r) for k, r in ents] == sorted((k.encode().lower(), r) for k, r in keys)
        assert sorted(n.find("mixer")) == [r for k, r in keys if k.lower() == "mixer"]
        # every node within capacity, header page count = file pages
        assert n.npages * 512 == os.path.getsize(p)

        def fill(pg):
            e, last = n._read(pg)
            assert len(e) <= n.kpn
            if not n._is_leaf(e, last):
                for c, _r, _k in e:
                    fill(c)
                fill(last)

        fill(n.root)


def test_ndx_find_equals_scan_on_the_project_index(SA):
    """FxMain's pgdynobj index (310 rows of 27 pages: a tree of more than one page)."""
    d = Dbf(os.path.join(SA, "pgdynobj.DBF"), writable=False)
    n = PR._open_ndx(SA, "pgdynobj")
    try:
        assert n.npages > 2
        via = sorted(i for i, _r in PR._page_records(d, n, "PAGE", WTP))
        scan = sorted(i for i, _r in d.find("PAGE", WTP))
        assert via == scan and via
    finally:
        n.close()
        d.close()


# --------------------------------------------------------------------------- register
def _trim(project, dst, pages, drop_lang=()):
    """A copy of ``project``'s register tables holding only ``pages``' rows
    (+ fresh indexes), pages.DBF / changes.DBF / PROJECT.CIT copied whole;
    pglang = its first 50 strings minus ``drop_lang``."""
    drop = {x.lower() for x in drop_lang}
    for f in ("pages.DBF", "changes.DBF", "PROJECT.CIT"):
        shutil.copy(PR._find_file(project, f), os.path.join(dst, f))
    for t in list(PR.PAGE_TABLES) + ["activex", "pglang"]:
        src = PR._find_file(project, t + ".DBF")
        if src is None:
            continue
        d = Dbf(src, writable=False)
        if t == "pglang":
            recs = [d.read(i) for i in range(min(d.nrec, 50))]
            recs = [r for r in recs if d.field(r, "NATIVE").lower() not in drop]
        else:
            n = PR._open_ndx(project, t)
            recs = [r for p in pages for _i, r in PR._page_records(d, n, "PAGE", p)]
            if n is not None:
                n.close()
        with open(src, "rb") as fh:
            head = bytearray(fh.read(d.hlen))
        struct.pack_into("<I", head, 4, len(recs))
        out = os.path.join(dst, t + ".DBF")
        with open(out, "wb") as fh:
            fh.write(bytes(head) + b"".join(recs) + b"\x1a")
        key = "NATIVE" if t == "pglang" else "PAGE"
        create_ndx(os.path.join(dst, t + ".NDX"), key, d.fmap[key][1])
        d.close()
        dd = Dbf(out)
        with Ndx(os.path.join(dst, t + ".NDX")) as nx:
            for i in range(dd.nrec):
                nx.insert(dd.field(dd.read(i), key), i + 1)
        dd.close()
    for p in pages:
        shutil.copy(os.path.join(project, p + ".ctg"), os.path.join(dst, p + ".ctg"))


def _add_page_keys(dst, page, from_page):
    """Give ``page`` of the trimmed copy ``dst`` the page-wide (blank-AN) pgkey rows of
    ``from_page`` (no fixture page has both page-wide and object keyboard rows: FxAnim has
    two page-wide ones, FxButtons twelve object ones). Returns the rows added."""
    with Dbf(os.path.join(dst, "pgkey.DBF")) as d:
        rows = [d.decode(d.read(i)) for i in range(d.nrec)]
        add = [
            dict(r, PAGE=page)
            for r in rows
            if r["PAGE"].strip().lower() == from_page.lower() and not r["AN"].strip()
        ]
        first = d.nrec
        d.append(d.encode_many(add))
    with Ndx(os.path.join(dst, "pgkey.NDX")) as nx:
        for k in range(len(add)):
            nx.insert(page, first + k + 1)
    return add


def _live(dst, table, page):
    d = Dbf(os.path.join(dst, table + ".DBF"), writable=False)
    try:
        return [(i, d.decode(r)) for i, r in d.find("PAGE", page) if r[:1] != b"*"]
    finally:
        d.close()


def _index_consistent(dst, table):
    d = Dbf(os.path.join(dst, table + ".DBF"), writable=False)
    with Ndx(os.path.join(dst, table + ".NDX")) as n:
        ents = n.entries()
        assert sorted(r for _k, r in ents) == list(range(1, d.nrec + 1))
        for k, r in ents:
            assert d.field(d.read(r - 1), n.expr).lower() == k.decode("cp1252").rstrip().lower()
    size = os.path.getsize(os.path.join(dst, table + ".DBF"))
    assert size == d.hlen + d.nrec * d.rlen + 1
    d.close()


def test_register_pristine_page_is_a_noop_on_page_tables(SA, tmp_path):
    """(The stored rows are our register's own: a second register of the page as it is
    changes nothing.)"""
    dst = str(tmp_path / "trim")
    os.makedirs(dst)
    _trim(SA, dst, [WTP, METERS])
    before = {
        t: open(os.path.join(dst, t + ".DBF"), "rb").read()
        for t in PR.PAGE_TABLES
        if os.path.exists(os.path.join(dst, t + ".DBF"))
    }
    rep = PR.register_page(dst, WTP, os.path.join(dst, WTP + ".ctg"))
    assert all(v["action"] in ("unchanged", "absent") for v in rep["tables"].values())
    for t, b in before.items():
        assert open(os.path.join(dst, t + ".DBF"), "rb").read() == b, t
    assert rep["changes"] == [{"NAME": "pages", "KEY": WTP, "RECORD_NO": str(rep["pages_recno"])}]
    ch = Dbf(os.path.join(dst, "changes.DBF"), writable=False)
    assert ch.decode(ch.read(ch.nrec - 1)) == {
        "NAME": "pages",
        "KEY": WTP,
        "RECORD_NO": str(rep["pages_recno"]),
    }
    ch.close()
    assert rep["seconds"] < 1.0


def test_register_edited_page_replaces_its_rows(SA, tmp_path):
    """FxShapes' FxNumber (a top-level number display) gets a new TXT_STR."""
    page = "FxShapes"
    dst = str(tmp_path / "trim")
    os.makedirs(dst)
    _trim(SA, dst, [page, METERS])
    meters_before = _live(dst, "pgdynobj", METERS)
    old = _live(dst, "pgdynobj", page)
    num = _an_of(os.path.join(dst, page + ".ctg"), "FxNumber")
    assert [
        r
        for _i, r in old
        if r["AN"] == num
        and r.get("TYPE") == "0x0005"
        and r.get("TXTMODE") == "3"
        and not r.get("GROUP")
        and r.get("TXT_STR") != "123.4"
    ]
    pc = parse_ctg(os.path.join(dst, page + ".ctg"))
    po = next(o for o in pc.page.objects if PR._an(o) == int(num))
    assert W.set_object_property(po, "TXT_STR", "123.4")
    data = W.serialize_ctg(pc.container, pc.page, pc.attributes)
    with Ndx(os.path.join(dst, "pgdynobj.NDX")) as n:
        indexed = len(n.find(page))
    rep = PR.register_page(dst, page, data)
    t = rep["tables"]["pgdynobj"]
    assert t["action"] == "updated" and t["old"] == len(old) == t["new"]
    hit = [i for i, r in old if r["AN"] == num]
    assert t["updated"] == [hit[0] + 1] and t["deleted"] == [] and t["appended"] == []
    new = _live(dst, "pgdynobj", page)
    assert [i for i, _r in new] == [i for i, _r in old]  # rewritten in place
    assert [r for _i, r in new if r["AN"] == num][0]["TXT_STR"] == "123.4"
    assert rep["tables"]["pgalmind"]["action"] in ("unchanged", "absent")
    assert _live(dst, "pgdynobj", METERS) == meters_before
    _index_consistent(dst, "pgdynobj")
    with Ndx(os.path.join(dst, "pgdynobj.NDX")) as n:
        assert len(n.find(page)) == indexed
    # registering the same bytes again changes nothing more
    rep2 = PR.register_page(dst, page, data)
    assert rep2["tables"]["pgdynobj"]["action"] == "unchanged"


def test_register_keeps_blank_an_rows(SA, tmp_path):
    """FxButtons (keyboard records) with FxAnim's two page-wide key rows added."""
    page = "FxButtons"
    dst = str(tmp_path / "trim")
    os.makedirs(dst)
    _trim(SA, dst, [page, "FxAnim"])
    assert len(_add_page_keys(dst, page, "FxAnim")) == 2
    blank = [r for _i, r in _live(dst, "pgkey", page) if not r["AN"]]
    assert len(blank) == 2
    pc = parse_ctg(os.path.join(dst, page + ".ctg"))
    rows = PR.page_rows(pc, page)
    assert rows["pgkey"], "the page has keyboard records"
    # force a rewrite: drop one generated key row by editing its command
    k = next(
        o
        for o in pc.page.objects
        if o.type_code == 0x100B
        and PR._an(o) is not None
        and (o.subtype_size & 0x10)
        and any(kk == "CMD" for kk, _v in PR._value_bag(o))
    )
    cmd = dict(PR._value_bag(k))["CMD"]
    assert W.set_object_property(k, "CMD", cmd + ";")
    rep = PR.register_page(dst, page, W.serialize_ctg(pc.container, pc.page, pc.attributes))
    assert rep["tables"]["pgkey"]["action"] == "updated"
    assert len(rep["tables"]["pgkey"]["updated"]) == 1
    after = _live(dst, "pgkey", page)
    assert [r for _i, r in after if not r["AN"]] == blank
    _index_consistent(dst, "pgkey")


def test_register_appends_activex_and_logs_it(SA, tmp_path):
    page = METERS
    dst = str(tmp_path / "trim")
    os.makedirs(dst)
    _trim(SA, dst, [page])
    # drop the page's activex rows so the register must append them
    d = Dbf(os.path.join(dst, "activex.DBF"))
    for i in range(d.nrec):
        d.delete(i)
    d.close()
    rep = PR.register_page(dst, page, os.path.join(dst, page + ".ctg"))
    assert [a["action"] for a in rep["activex"]] == ["appended", "appended"]
    names = [c["NAME"] for c in rep["changes"]]
    assert names == ["pages", "activex", "activex"]
    live = [r for _i, r in _live(dst, "activex", page)]
    assert [(r["AN"], r["OBJ_NAME"]) for r in live] == _gauges(SA)
    _index_consistent(dst, "activex")
    # a second register finds them and writes nothing to activex
    rep2 = PR.register_page(dst, page, os.path.join(dst, page + ".ctg"))
    assert rep2["activex"] == [] and [c["NAME"] for c in rep2["changes"]] == ["pages"]


def test_register_marks_project_uncompiled(SA, tmp_path):
    from ctgedit.io import projectcit

    dst = str(tmp_path / "trim")
    os.makedirs(dst)
    _trim(SA, dst, [WTP])
    prev = PR._set_comp_state(os.path.join(dst, "PROJECT.CIT"), b"COMPILED")
    assert prev is not None
    rep = PR.register_page(dst, WTP, os.path.join(dst, WTP + ".ctg"))
    assert rep["compstate"] == {"previous": "COMPILED", "now": "UNCOMPILED"}
    # the compound file still reads back (every stream intact)
    assert PR._set_comp_state(os.path.join(dst, "PROJECT.CIT"), b"UNCOMPILED") == "UNCOMPILED"
    # (a second reader: projectcit's own walk of the compound file, not the patcher's)
    streams = projectcit.read_streams(os.path.join(dst, "PROJECT.CIT"))
    assert streams["CompState"][:10] == b"UNCOMPILED"
    assert streams["Name"][:6] == b"FxMain"


def test_register_adds_missing_native_strings(SA, tmp_path):
    dst = str(tmp_path / "trim")
    os.makedirs(dst)
    page = "FxAnim"
    _trim(SA, dst, [page], drop_lang=["Fx Stop", "Fx Mode"])
    want = PR.lang_strings(os.path.join(dst, page + ".ctg"))
    d = Dbf(os.path.join(dst, "pglang.DBF"), writable=False)
    have = {d.decode(d.read(i))["NATIVE"].lower() for i in range(d.nrec)}
    d.close()
    missing = [x for x in want if x.lower() not in have]
    assert missing == ["Fx Stop", "Fx Mode"]
    rep = PR.register_page(dst, page, os.path.join(dst, page + ".ctg"))
    assert rep["pglang"] == missing
    rep2 = PR.register_page(dst, page, os.path.join(dst, page + ".ctg"))
    assert rep2["pglang"] == []
    with Ndx(os.path.join(dst, "pglang.NDX")) as n:
        for x in missing:
            assert n.find(x)


def test_register_deletes_rows_of_removed_objects_and_appends_new(SA, tmp_path):
    """FxShapes' FxNumber loses its dynamic flag, then gets it back."""
    page = "FxShapes"
    dst = str(tmp_path / "trim")
    os.makedirs(dst)
    _trim(SA, dst, [page])
    an = int(_an_of(os.path.join(dst, page + ".ctg"), "FxNumber"))
    pc = parse_ctg(os.path.join(dst, page + ".ctg"))
    objs = pc.page.objects
    # drop the dynamic flag of FxNumber (its row must go) ...
    po = next(o for o in objs if PR._an(o) == an)
    po.subtype_size &= ~0x10
    rep = PR.register_page(dst, page, pc)
    t = rep["tables"]["pgdynobj"]
    assert len(t["deleted"]) == 1 and not t["updated"] and not t["appended"]
    assert all(r["AN"] != str(an) for _i, r in _live(dst, "pgdynobj", page))
    # ... and back: a row for it is appended (the deleted one stays deleted)
    po.subtype_size |= 0x10
    rep = PR.register_page(dst, page, pc)
    t = rep["tables"]["pgdynobj"]
    assert len(t["appended"]) == 1 and not t["deleted"]
    live = _live(dst, "pgdynobj", page)
    assert live[-1][1]["AN"] == str(an) and live[-1][0] + 1 == t["appended"][0]
    _index_consistent(dst, "pgdynobj")


def test_ndx_insert_many_builds_the_same_index_as_one_insert_each(tmp_path):
    """Ndx.insert_many (a page's first save registers its rows at once) leaves the index
    byte for byte as one insert() per row does: pages split the same way, the header once."""
    import random
    from ctgedit.format.dbf import Ndx, create_ndx

    rng = random.Random(7)
    keys = [
        "%s_%d" % (rng.choice(["Pump", "Valve", "AN", "Tank", "Motor"]), rng.randrange(5000))
        for _ in range(3000)
    ]
    items = [(k, i + 1) for i, k in enumerate(keys)]
    a, b = str(tmp_path / "a.ndx"), str(tmp_path / "b.ndx")
    for p in (a, b):
        create_ndx(p, "NAME", 16)
    with Ndx(a) as nx:
        for k, r in items:
            nx.insert(k, r)
    with Ndx(b) as nx:
        nx.insert_many(items)
    assert open(a, "rb").read() == open(b, "rb").read()
    with Ndx(b, writable=False) as nx:
        assert sorted(r for _k, r in nx.entries()) == list(range(1, len(items) + 1))
        assert nx.find(keys[123]) and 124 in nx.find(keys[123])


def test_a_page_without_a_pages_row_gets_no_register_rows(SA, tmp_path):
    """A project page whose pages.DBF row is missing (its registration was rolled back) is
    not registered: nothing compiles it without that row, and the Project Editor writes the
    row first. Its page tables stay as they were."""
    dst = str(tmp_path / "trim")
    os.makedirs(dst)
    _trim(SA, dst, [METERS])  # WTP's rows are not there
    shutil.copy(os.path.join(SA, WTP + ".ctg"), os.path.join(dst, "NoRow.ctg"))
    before = {
        t: open(os.path.join(dst, t + ".DBF"), "rb").read()
        for t in PR.PAGE_TABLES
        if os.path.exists(os.path.join(dst, t + ".DBF"))
    }
    rep = PR.register_page(dst, "NoRow", os.path.join(dst, "NoRow.ctg"))
    assert rep.get("unregistered") and rep["pages_recno"] is None
    for t, b in before.items():
        assert open(os.path.join(dst, t + ".DBF"), "rb").read() == b, t


def test_a_member_keeps_txtmode_zero_with_nothing_of_its_own_after_txt_off(SA):
    """Example Genies AN 532 keeps TXTMODE '0' (only '!*' keys between TXT_OFF and TXTMODE);
    !ServerMonitor's members drop it (VISIBLE between)."""
    from ctgedit.format.page import parse_ctg

    o = next(
        o
        for o in parse_ctg(os.path.join(SA, "FxParts.ctg")).page.objects
        if o.type_code == 0x05 and len(GP.decode(o).entries) == 2 and len(GP.decode(o).entries[0]) >= 3
    )
    r = GP.decode(o)
    state = [("!*PropSheet.Focus", "#1402.#1403"), ("!*#1402", "#1403")]
    r.entries[0][2] = GP.Bag([("TXT_OFF", "%OffLabel%")] + state + [("TXTMODE", "0")], r.entries[0][2].ov)

    def dropped(bag):
        r.entries[0][1] = GP.Bag(bag, r.entries[0][1].ov)
        return PR._txtmode_dropped(GP.to_po(GP.encode(r)), bag)

    assert not dropped(state + [("TXT_OFF", "@(OFF)"), ("TXTMODE", "0")])
    assert dropped(state + [("TXT_OFF", "@(OFF)"), ("VISIBLE", "X = 1"), ("TXTMODE", "0")])
