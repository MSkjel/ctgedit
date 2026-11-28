"""Derive speed-ups change no output."""

from ctgedit.format.fileio import find_ci
import copy
import os
import pickle
import random
import shutil
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.format import colourstate as CS  # noqa: E402
from ctgedit.format import page as PG  # noqa: E402
from ctgedit.format.dbf import Dbf  # noqa: E402
from ctgedit.format.symbols import SymbolLibrary  # noqa: E402
from ctgedit.format.textcodec import UnencodableTextError  # noqa: E402
from ctgedit.model.document import ObjectKind  # noqa: E402
from ctgedit.io.readerparts import graft  # noqa: E402

#: the pages of the sweeps: (page, project) of the fake install
PAGES = (("FxLarge", "FxPerf"), ("FxGenies", "FxMain"), ("FxLegacy", "FxMain"))
#: the libraries of the sweeps: (project, file)
GENIES_CTM = ("FxLib", "fxparts.ctm")
SWITCHES_CTL = ("FxLib", "fxsym.ctl")


@pytest.fixture
def large(fx_page):
    """FxPerf's FxLarge in the fx_install copy (970 of its records are styled)."""
    return fx_page("FxLarge", "FxPerf")


def _sweep_objects(fx_page):
    out = []
    for name, project in PAGES:
        out += PG.parse_ctg(fx_page(name, project)).page.objects
    return out


# --------------------------------------------------------------------------- the layout memo
def _walk(po):
    """layout without the memo (a record copy without the memo attribute)."""
    c = copy.copy(po)
    c.__dict__.pop(CS.LAYOUT_MEMO_ATTR, None)
    c.body_raw = bytes(bytearray(po.body_raw))  # another body object: no memo hit
    return CS._layout(c.body_raw, c.subtype_size)


def test_layout_memo_is_the_walk(fx_page, large):
    for po in _sweep_objects(fx_page):
        want = _walk(po)
        first = CS.layout(po)
        assert first == want
        if first is not None:
            first["at"] = -5  # a caller's change stays the caller's
        again = CS.layout(po)
        assert again == want
        if again is not None:
            assert again is not first
    objs = PG.parse_ctg(large).page.objects
    for po in objs:
        want = _walk(po)
        first = CS.layout(po)
        assert first == want
        if first is not None:
            first["at"] = -5  # a caller's change stays the caller's
        again = CS.layout(po)
        assert again == want
        if again is not None:
            assert again is not first
    # the memo follows the body and the flags
    po = next(o for o in objs if CS.layout(o) is not None and o.subtype_size & 0x2)
    before = CS.layout(po)
    po.subtype_size &= ~0x2
    assert CS.layout(po) == CS._layout(po.body_raw, po.subtype_size)
    assert "style_at" not in CS.layout(po) and "style_at" in before
    po.subtype_size |= 0x2
    po.body_raw = po.body_raw[:12]  # a body that no longer walks
    assert CS.layout(po) is None
    # a mutable body is never kept
    po2 = copy.copy(objs[1])
    po2.__dict__.pop(CS.LAYOUT_MEMO_ATTR, None)
    po2.body_raw = bytearray(po2.body_raw)
    assert CS.layout(po2) == CS._layout(bytes(po2.body_raw), po2.subtype_size)
    assert CS.LAYOUT_MEMO_ATTR not in po2.__dict__


def test_slots_memo_is_read_slots(fx_page):
    from ctgedit.format import placement as PL

    for po in _sweep_objects(fx_page):
        try:
            want = PL.read_slots(bytes(bytearray(po.body_raw)))
        except Exception as e:  # noqa: BLE001 -- raises alike, every time
            for _ in range(2):
                with pytest.raises(type(e)):
                    PL.read_slots_of(po)
            continue
        first = PL.read_slots_of(po)
        assert first == want
        first[0].clear()  # a caller's change stays the caller's
        assert PL.read_slots_of(po) == want
    po.body_raw = po.body_raw[:5]  # another body: read again
    with pytest.raises(Exception):
        PL.read_slots_of(po)


# --------------------------------------------------------------------------- the lazy parse
def _fields(o):
    return tuple(getattr(o, n) for n in PG.PageObject.__dataclass_fields__)


def _blocks(path):
    lib = SymbolLibrary.open(path)
    assert lib.element_names(), path
    for name in lib.element_names():
        blk = lib._find_block(name)
        if blk is not None and blk.size > 0:
            yield blk, lib.container.word2


@pytest.mark.parametrize("where", [GENIES_CTM, SWITCHES_CTL] + list(PAGES))
def test_lazy_parse_is_the_parse(where, fx_install):
    path = fx_install.page(*where) if where in PAGES else fx_install.file(*where)
    if path.endswith(".ctg"):
        from ctgedit.format.container import Container

        c = Container.parse(open(path, "rb").read(), keep_raw=False)
        PG.upgrade.upgrade_container(c)
        blocks = [(c.block("page"), c.word2)]
    else:
        blocks = list(_blocks(path))
    n = 0
    for blk, word2 in blocks:
        eager = PG.parse_page(blk, word2).objects
        lazy = PG.parse_page(blk, word2, lazy=True).objects
        assert len(eager) == len(lazy)
        for k, (a, b) in enumerate(zip(eager, lazy)):
            assert isinstance(b, PG.LazyPageObject)
            early = copy.copy(b)  # taken before the scan
            deep = copy.deepcopy(b)
            state = dict(b.__dict__)  # an undo's record state
            assert _fields(a) == _fields(b)
            assert a == b and b == a and not (a != b)
            assert repr(a) == repr(b)
            assert _fields(early) == _fields(a) and _fields(deep) == _fields(a)
            assert a.raw_properties() == b.raw_properties()
            b.__dict__.clear()
            b.__dict__.update(state)  # put back: scanned again, alike
            assert _fields(b) == _fields(a)
            if k % 25 == 0:
                assert _fields(pickle.loads(pickle.dumps(early))) == _fields(a)
            n += 1
    assert n > 10


def test_lazy_record_keeps_what_was_set_before_its_scan(fx_install):
    blk, word2 = next(_blocks(fx_install.file(*GENIES_CTM)))
    eager = PG.parse_page(blk, word2).objects
    lazy = PG.parse_page(blk, word2, lazy=True).objects
    a, b = eager[1], lazy[1]
    b.bags = ()  # set before anything was read
    assert b.bags == () and b.text_exprs == a.text_exprs and b.font_face == a.font_face
    c = lazy[2]
    c.body_raw = b"\x00" * 8  # an edit before the scan: the parse's
    assert c.bags == eager[2].bags  # bags, as a parse at load had them


def test_lazy_parse_is_off_with_the_coverage_diagnostic(monkeypatch, fx_install):
    monkeypatch.setattr(PG, "WANT_COVERAGE", True)
    blk, word2 = next(_blocks(fx_install.file(*GENIES_CTM)))
    assert not any(isinstance(o, PG.LazyPageObject) for o in PG.parse_page(blk, word2, lazy=True).objects)


# --------------------------------------------------------------------------- Dbf.encode_many
def test_encode_many_is_encode(tmp_path, fx_install):
    src = tmp_path / "pgdynobj.DBF"
    shutil.copy(fx_install.file("FxMain", "pgdynobj.DBF"), src)
    with Dbf(str(src), writable=False) as d:
        names = [n for n, _o, _ln in d.fields]
        rnd = random.Random(7)
        rows = []
        for k in range(400):
            row = {}
            for n in rnd.sample(names, rnd.randint(0, len(names))):
                key = n if rnd.random() < 0.8 else n.lower()
                v = rnd.choice(
                    ["", "x", "Pump_%d" % k, "a" * 300, "Æøå %d" % k, " lead", "trail  ", "0", "=Tag1 > 5"]
                )
                row[key] = v
            if rnd.random() < 0.2 and names:
                row[names[0].lower()] = "dup %d" % k  # two keys of one field
                row[names[0]] = "last %d" % k
            row["NOT_A_FIELD"] = "ignored"
            rows.append(row)
        assert d.encode_many(rows) == [d.encode(r) for r in rows]
        assert d.encode_many(rows, b"*") == [d.encode(r, b"*") for r in rows]
        bad = [{names[0]: "ok"}, {names[1]: "\u4e2d", names[0]: "x"}]
        with pytest.raises(UnencodableTextError) as e1:
            [d.encode(r) for r in bad]
        with pytest.raises(UnencodableTextError) as e2:
            d.encode_many(bad)
        assert str(e1.value) == str(e2.value)


# --------------------------------------------------------------------------- hotspots
def _reference_hotspots(objs, only=None):
    """graft.suppress_redundant_hotspots as it was (every box against every hotspot)."""
    from collections import defaultdict

    n = len(objs)
    order = range(n) if only is None else sorted(i for i in only if i < n)
    root = {}
    for i in order:
        j, seen = i, 0
        p = objs[j].parent
        while p is not None and 0 <= p < n and seen < n:
            j, p, seen = p, objs[p].parent, seen + 1
        root[i] = j
    closed = (ObjectKind.RECTANGLE, ObjectKind.POLYGON, ObjectKind.ELLIPSE)
    boxes = defaultdict(list)
    for i in order:
        o = objs[i]
        if o.style.filled and o.kind in closed:
            g = o.geometry
            boxes[root[i]].append((g.x, g.y, g.x + g.extent_x, g.y + g.extent_y))
    out = set()
    for i in order:
        o = objs[i]
        if not (
            o.kind == ObjectKind.RECTANGLE
            and o.members
            and not o.style.filled
            and o.style.line_colour is None
            and (o.raw.get("_dwflags") or 0) & 0x10
        ):
            continue
        g = o.geometry
        ax0, ay0, ax1, ay1 = g.x, g.y, g.x + g.extent_x, g.y + g.extent_y
        area = (ax1 - ax0) * (ay1 - ay0)
        if area <= 0:
            continue
        for bx0, by0, bx1, by1 in boxes[root[i]]:
            ix = max(0, min(ax1, bx1) - max(ax0, bx0))
            iy = max(0, min(ay1, by1) - max(ay0, by0))
            if ix * iy >= 0.4 * area:
                out.add(i)
                break
    return out


def _random_objects(rnd, n):
    kinds = [ObjectKind.RECTANGLE, ObjectKind.POLYGON, ObjectKind.ELLIPSE, ObjectKind.TEXT, ObjectKind.GROUP]
    objs = []
    for i in range(n):
        r = rnd.random()
        # (mostly a forest's parents, which come first; a few that point forward or at
        # the record itself: a cycle the walk's step limit ends)
        parent = None if i == 0 or r < 0.15 else rnd.randrange(i) if r < 0.95 else rnd.randrange(n + 2)
        w, h = rnd.randint(-3, 60), rnd.randint(-3, 60)
        objs.append(
            SimpleNamespace(
                parent=parent,
                kind=rnd.choice(kinds),
                members=[1] if rnd.random() < 0.6 else [],
                geometry=SimpleNamespace(
                    x=rnd.randint(0, 300), y=rnd.randint(0, 300), extent_x=w, extent_y=h
                ),
                style=SimpleNamespace(
                    filled=rnd.random() < 0.5, line_colour=None if rnd.random() < 0.7 else 0
                ),
                raw={"_dwflags": rnd.choice([0, 0x10, 0x12])},
            )
        )
    return objs


def test_hotspot_pass_finds_what_the_full_search_found():
    rnd = random.Random(11)
    for trial in range(60):
        objs = _random_objects(rnd, rnd.randint(1, 160))
        only = None
        if trial % 3 == 0:
            only = {i for i in range(len(objs)) if rnd.random() < 0.7}
        want = _reference_hotspots(objs, only)
        graft.suppress_redundant_hotspots(objs, only)
        got = {i for i, o in enumerate(objs) if o.raw.get("_no_outline")}
        assert got == want


# --------------------------------------------------------------------------- the page model
@pytest.mark.parametrize("where", PAGES)
def test_page_model_of_a_lazy_document_is_the_model(where, fx_page):
    path = fx_page(*where)
    from ctgedit.editing import EditableDocument

    data = open(path, "rb").read()
    a = EditableDocument.from_bytes(data, path, records_only=True).export_page_model()
    b = EditableDocument.from_bytes(data, path, records_only=True, lazy_bags=True).export_page_model()
    a.pop("generator", None)
    b.pop("generator", None)
    assert a == b


# --------------------------------------------------------------------------- the .ctF's decode
@pytest.mark.parametrize("where", [("FxLarge", "FxPerf"), ("FxGenies", "FxMain"), GENIES_CTM])
def test_value_bag_only_decode_is_the_decode_of_that_bag(where, fx_install):
    from ctgedit.format import ctf
    from ctgedit.format import genie_place as GP

    path = fx_install.page(*where) if where in PAGES else fx_install.file(*where)
    if path.endswith(".ctg"):
        objs = PG.parse_ctg(path).page.objects
    else:
        objs = [o for blk, w in _blocks(path) for o in PG.parse_page(blk, w).objects]
    n = 0
    for po in objs:
        for body in (po.body_raw, po.body_raw[: len(po.body_raw) // 2]):  # whole, cut short
            c = copy.copy(po)
            c.__dict__.pop(CS.LAYOUT_MEMO_ATTR, None)
            c.body_raw = body
            try:
                full = GP.decode(c)
            except Exception as e:  # noqa: BLE001 -- the light decode fails alike
                with pytest.raises(type(e)):
                    GP.decode(c, value_bag_only=True)
                continue
            light = GP.decode(c, value_bag_only=True)
            for f in GP.Rec.__dataclass_fields__:
                if f != "entries":
                    assert getattr(light, f) == getattr(full, f), f
            if full.entries is None:
                assert light.entries is None
                continue
            assert [len(e) for e in light.entries] == [len(e) for e in full.entries]
            assert light.entries[0][1] == full.entries[0][1]
            # the bake's lookup: Bag.get's first match without case
            o = ctf._Obj(c, light)
            keys = {k for k, _v in full.entries[0][1].pairs} | {"VISIBLE", "visible", "COLMODE"}
            for k in keys:
                for kk in (k, k.lower(), k.upper()):
                    assert o.get(kk) == full.entries[0][1].get(kk)
            n += 1
    assert n > 10


# --------------------------------------------------------------------------- compare_rows
def _reference_compare_rows(project_dir, page, tables):
    """sim/pagemodel.compare_rows as it was (every record hashed)."""
    import collections
    from ctgedit.io import pgregister as PR
    from ctgedit.sim import pagemodel as PM

    out = {}
    for table, rows in tables.items():
        path = find_ci(project_dir, table + ".DBF")
        if path is None:
            continue
        with Dbf(path, writable=False) as d:
            if "PAGE" not in d.fmap:
                continue
            ndx = PR.open_ndx(project_dir, table, writable=False)
            try:
                have = [r for _i, r in sorted(PR.page_records(d, ndx, "PAGE", page)) if r[:1] != b"*"]
            finally:
                if ndx is not None:
                    ndx.close()
            if table == "activex":
                ans = {r.get("AN") for r in rows}
                have = [r for r in have if (d.field(r, "AN") if "AN" in d.fmap else None) in ans]
            try:
                enc = [d.encode(r) for r in rows]
            except Exception:  # noqa: BLE001
                out[table] = (len(have), 0, 0)
                continue
            po, pl = d.fmap["PAGE"]

            def norm(rec):
                return b" " + rec[1:po] + rec[po : po + pl].lower() + rec[po + pl :]

            want = collections.Counter(norm(r) for r in enc)
            got = collections.Counter(norm(r) for r in have)
            same = want & got
            equal = sum(same.values())
            rest_w, rest_g = want - same, got - same
            if rest_w and rest_g:
                kw, kg = collections.Counter(), collections.Counter()
                for r, c in rest_w.items():
                    kw[PM._key(d.decode(r))] += c
                for r, c in rest_g.items():
                    kg[PM._key(d.decode(r))] += c
                equal += sum((kw & kg).values())
        out[table] = (len(have), len(enc), equal)
    return out


def test_compare_rows_is_the_hashed_comparison(monkeypatch, fx_page, fx_project):
    """On FxMain's FxGenies: 70 pgdynobj rows, pgtouch, pgkey and pgbutton rows."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pgregister as PR
    from ctgedit.sim import pagemodel as PM

    page_path = fx_page("FxGenies")
    EXAMPLE = fx_project("FxMain")
    doc = EditableDocument.from_bytes(open(page_path, "rb").read(), page_path, records_only=True)
    name = PM._page_name(doc)
    tables = PM.page_tables(doc, name, [])
    assert len(tables["pgdynobj"]) > 50 and tables["pgtouch"] and tables["pgkey"]
    rnd = random.Random(5)

    def variants():
        yield tables, name  # every row stored
        yield tables, name.upper()  # another PAGE spelling
        for _ in range(4):
            t2 = {}
            for k, rows in tables.items():
                rows = [dict(r) for r in rows]
                rnd.shuffle(rows)
                for r in rows[: len(rows) // 5]:
                    key = rnd.choice(sorted(r))
                    r[key] = (r[key] or "") + " x"  # a changed field
                rows = rows[len(rows) // 10 :] + rows[: len(rows) // 20]  # dropped, repeated
                t2[k] = rows
            yield t2, name

    for t2, page in variants():
        assert PM.compare_rows(EXAMPLE, page, t2) == _reference_compare_rows(EXAMPLE, page, t2)
    # stored records with odd flag bytes and PAGE spellings
    real = PR.page_records

    def odd(dbf, ndx, field, value):
        out = []
        for k, (i, b) in enumerate(real(dbf, ndx, field, value)):
            if k % 7 == 0:
                b = b"?" + b[1:]
            elif k % 11 == 0:
                o, ln = dbf.fmap["PAGE"]
                b = b[:o] + b[o : o + ln].upper() + b[o + ln :]
            out.append((i, b))
        return out

    monkeypatch.setattr(PR, "page_records", odd)
    assert PM.compare_rows(EXAMPLE, name, tables) == _reference_compare_rows(EXAMPLE, name, tables)


def test_page_records_read_in_runs_are_the_records(fx_install):
    """FxMain's pgdynobj.DBF (310 rows: every run below is inside it)."""
    from ctgedit.io import pgregister as PR

    with Dbf(fx_install.file("FxMain", "pgdynobj.DBF"), writable=False) as d:
        assert d.nrec > 70
        idx = list(range(0, min(d.nrec, 40))) + list(range(min(d.nrec, 60), min(d.nrec, 70)))
        idx += [d.nrec - 1, 3, 3]
        got = PR._read_records(d, idx)
        assert got == {i: d.read(i) for i in idx}
