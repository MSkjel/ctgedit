"""Reader speed-ups change no output."""

import ast
import os
import struct
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.format import dbfcols  # noqa: E402
from ctgedit.format import projects as PR  # noqa: E402
from ctgedit.format.textcodec import CODEC  # noqa: E402
from ctgedit.io import project as PJ  # noqa: E402
from ctgedit.io import reader  # noqa: E402


@pytest.fixture
def large(fx_page):
    """FxPerf's FxLarge in the fx_install copy."""
    return fx_page("FxLarge", "FxPerf")


@pytest.fixture(autouse=True)
def _fresh_caches():
    dbfcols.clear()
    yield
    dbfcols.clear()


@pytest.fixture(params=["whole", "streamed"])
def reading(request, monkeypatch):
    """Tables read whole, and streamed in chunks of a few records (the big-table path)."""
    if request.param == "streamed":
        monkeypatch.setattr(dbfcols, "_STREAM_MIN", 0)
        monkeypatch.setattr(dbfcols, "_CHUNK", 100)
    return request.param


# --------------------------------------------------------------------------- references
def _reference_read_dbf_rows(path):
    """format.projects.read_dbf_rows as it was."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return []
    if len(data) < 33:
        return []
    try:
        nrec = struct.unpack_from("<I", data, 4)[0]
        hdr_size = struct.unpack_from("<H", data, 8)[0]
        rec_size = struct.unpack_from("<H", data, 10)[0]
    except struct.error:
        return []
    if rec_size <= 0 or hdr_size < 33 or hdr_size > len(data):
        return []
    fields = []
    off, pos = 32, 1
    while off + 32 <= hdr_size and data[off] != 0x0D:
        name = data[off : off + 11].split(b"\x00")[0].decode(CODEC, "replace").strip()
        length = data[off + 16]
        fields.append((name, pos, length))
        pos += length
        off += 32
    if not fields or pos > rec_size:
        return []
    rows = []
    rec_off = hdr_size
    for _ in range(nrec):
        if rec_off + rec_size > len(data):
            break
        rec = data[rec_off : rec_off + rec_size]
        rec_off += rec_size
        if rec[0] == 0x2A:
            continue
        rows.append({name: rec[p : p + ln].decode(CODEC, "replace").strip() for name, p, ln in fields})
    return rows


def _dbfread_rows(path, columns):
    from dbfread import DBF

    return [
        tuple(r.get(c) for c in columns)
        for r in DBF(
            path, encoding=CODEC, load=False, ignore_missing_memofile=True, char_decode_errors="replace"
        )
    ]


def _reference_variable_tags(path):
    """Project.variable_tags as it was (dbfread), as value tuples."""
    from dbfread import DBF

    out = []
    for r in DBF(
        path, encoding=CODEC, load=False, ignore_missing_memofile=True, char_decode_errors="replace"
    ):
        name = (r.get("NAME") or "").strip()
        if not name:
            continue
        out.append(
            (
                name,
                (r.get("TYPE") or "").strip(),
                (r.get("UNIT") or "").strip(),
                (r.get("ADDR") or "").strip(),
                (r.get("ENG_UNITS") or "").strip(),
                (r.get("COMMENT") or "").strip(),
                (r.get("CLUSTER") or "").strip(),
                (r.get("EQUIP") or "").strip(),
                (r.get("ITEM") or "").strip(),
                (r.get("FORMAT") or "").strip(),
                str(r.get("ENG_ZERO") or "").strip(),
                str(r.get("ENG_FULL") or "").strip(),
            )
        )
    return out


def _tag_values(tags):
    return [tuple(vars(t).values()) for t in tags]


# --------------------------------------------------------------------------- a table writer
def _dbf(fields, rows, *, flags=None, nrec=None, rec_size=None, term=b"\x0d\x00", eof=b"\x1a", tail=b""):
    """dBASE III bytes: ``fields`` = [(name bytes, type, length[, decimals])], ``rows`` = one
    tuple of raw field bytes each (padded / cut to the field length)."""
    desc = b""
    for f in fields:
        name, typ, ln = f[:3]
        dec = f[3] if len(f) > 3 else 0
        desc += (
            name.ljust(11, b"\x00")[:11] + typ.encode() + b"\x00" * 4 + bytes((ln & 0xFF, dec)) + b"\x00" * 14
        )
    lengths = [(f[2] & 0xFF) | ((f[3] if len(f) > 3 else 0) << 8 if f[1] == "C" else 0) for f in fields]
    total = 1 + sum(lengths)
    rs = rec_size if rec_size is not None else total
    hs = 32 + len(desc) + len(term)
    recs = b""
    for k, r in enumerate(rows):
        flag = flags[k] if flags else b" "
        body = b"".join(v.ljust(ln, b" ")[:ln] for v, ln in zip(r, lengths))
        recs += (flag + body).ljust(rs, b" ")[:rs]
    head = struct.pack("<BBBBIHH", 3, 126, 9, 29, len(rows) if nrec is None else nrec, hs, rs)
    head += b"\x00" * (32 - len(head))
    return head + desc + term + recs + eof + tail


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


FIELDS = [(b"NAME", "C", 12), (b"TYPE", "C", 6), (b"COMMENT", "C", 20), (b"FORMAT", "C", 8)]
ROWS = [
    (b"Tag1", b"DIGITAL", b"plain", b"###.#"),
    (b"  Tag2 ", b"INT", b"\xa0nbsp\xa0 ", b"#.##"),
    (b"Tag3\x00\x00", b"REAL", b"x\x00y\x00\x00", b""),  # NUL padding / inside
    (b"\x1cTag4\x1f", b"", b"\x80 euro \x9f", b"##"),  # str whitespace, 0x80..
    (b"", b"REAL", b"no name", b"#"),
    (b"Tag6", b"STRING", b"\x85\x81\x8d", b"   "),
]


def _check_citect(path, columns_sets=(("NAME", "FORMAT"), ("FORMAT", "NOPE", "NAME"))):
    ref = _reference_read_dbf_rows(path)
    assert PR.read_dbf_rows(path) == ref
    assert dbfcols.citect_rows(path) == ref
    assert dbfcols.rows(path) == ref
    for cols in columns_sets:
        assert dbfcols.citect_rows(path, cols) == [tuple(r.get(c, "") for c in cols) for r in ref]
    return ref


def _check_dbfread(path, columns=("NAME", "TYPE", "COMMENT", "FORMAT", "NOPE")):
    ref = _dbfread_rows(path, columns)
    # read by the column slicer itself (not handed to dbfread)
    assert dbfcols.dbfread_columns(path, columns, fallback=False) == ref
    assert dbfcols.dbfread_columns(path, columns) == ref
    return ref


# --------------------------------------------------------------------------- the dialects
def test_citect_dialect_equals_read_dbf_rows(tmp_path, reading):
    p = _write(tmp_path, "t.dbf", _dbf(FIELDS, ROWS, flags=[b" ", b"*", b" ", b"X", b" ", b" "]))
    ref = _check_citect(p)
    assert [r["NAME"] for r in ref] == ["Tag1", "Tag3\x00\x00", "Tag4", "", "Tag6"]
    # the record count of the header rules: fewer, more than present, a cut last record
    for nrec in (2, 9):
        _check_citect(_write(tmp_path, "n%d.dbf" % nrec, _dbf(FIELDS, ROWS, nrec=nrec)))
    data = _dbf(FIELDS, ROWS)
    _check_citect(_write(tmp_path, "cut.dbf", data[:-20]))
    # a record longer than its fields, a duplicate column (the last one wins)
    _check_citect(_write(tmp_path, "pad.dbf", _dbf(FIELDS, ROWS, rec_size=60)))
    dup = FIELDS + [(b"NAME", "C", 5)]
    _check_citect(_write(tmp_path, "dup.dbf", _dbf(dup, [r + (b"dupnm",) for r in ROWS])))
    # malformed tables read as nothing
    assert dbfcols.citect_rows(_write(tmp_path, "short.dbf", data[:20])) == []
    assert dbfcols.citect_rows(str(tmp_path / "missing.dbf")) == []
    bad = bytearray(data)
    struct.pack_into("<H", bad, 8, len(bad) + 5)
    assert dbfcols.citect_rows(_write(tmp_path, "bad.dbf", bytes(bad))) == []


def test_dbfread_dialect_equals_dbfread(tmp_path, reading):
    rows = ROWS + [(b"Tag7", b"LONG", b"after eof", b"#")]
    data = _dbf(FIELDS, rows, flags=[b" ", b"*", b"#", b" ", b" ", b" ", b" "])
    _check_dbfread(_write(tmp_path, "a.dbf", data))
    # 0x1A as a record flag stops the walk; records past the header count are read
    stop = _dbf(FIELDS, rows, flags=[b" ", b" ", b"\x1a", b" ", b" ", b" ", b" "])
    assert len(_check_dbfread(_write(tmp_path, "stop.dbf", stop))) == 2
    assert len(_check_dbfread(_write(tmp_path, "more.dbf", _dbf(FIELDS, rows, nrec=2)))) == 7
    # a cut last record (a partial row), no EOF marker, garbage after the marker
    _check_dbfread(_write(tmp_path, "cut.dbf", _dbf(FIELDS, rows, eof=b"")[:-9]))
    _check_dbfread(_write(tmp_path, "tail.dbf", _dbf(FIELDS, rows, tail=b" junk")))
    # a record size other than the fields' (dbfread's own walk), a long C field
    _check_dbfread(_write(tmp_path, "stride.dbf", _dbf(FIELDS, rows, rec_size=70)))
    long = [(b"NAME", "C", 44, 1), (b"FORMAT", "C", 8)]
    _check_dbfread(
        _write(tmp_path, "long.dbf", _dbf(long, [(b"x" * 290 + b"end", b"#")])), ("NAME", "FORMAT")
    )
    # descriptors ended by LF, duplicate names
    _check_dbfread(_write(tmp_path, "lf.dbf", _dbf(FIELDS, rows, term=b"\x0a")))
    dup = FIELDS + [(b"NAME", "C", 5)]
    _check_dbfread(_write(tmp_path, "dup.dbf", _dbf(dup, [r + (b"dupnm",) for r in rows])))


def test_dbfread_dialect_leaves_other_field_types_to_dbfread(tmp_path, reading):
    fields = [(b"NAME", "C", 10), (b"ENG_ZERO", "N", 6), (b"FORMAT", "C", 6)]
    p = _write(tmp_path, "n.dbf", _dbf(fields, [(b"T1", b"   0", b"##"), (b"T2", b"12.5", b"#")]))
    cols = ("NAME", "ENG_ZERO", "FORMAT")
    assert dbfcols.dbfread_columns(p, cols) == _dbfread_rows(p, cols)
    assert dbfcols.dbfread_columns(p, cols, fallback=False) is None
    # ... and so does Project: its formats come from the tag list then
    import shutil as _sh

    d = tmp_path / "proj"
    d.mkdir()
    _sh.copy(p, d / "variable.dbf")
    proj = PJ.Project(str(d))
    assert dict(proj.variable_formats()) == {t.name.lower(): t.format for t in proj.variable_tags()}
    # a truncated header: dbfread's own error
    short = _write(tmp_path, "short.dbf", b"\x03" * 20)
    with pytest.raises(Exception):
        _dbfread_rows(short, ("NAME",))
    with pytest.raises(Exception):
        dbfcols.dbfread_columns(short, ("NAME",))


def test_streamed_table_with_a_long_header_is_read_by_dbfread(tmp_path, monkeypatch):
    """Descriptors past the header bytes read first: dbfread reads the table itself."""
    monkeypatch.setattr(dbfcols, "_STREAM_MIN", 0)
    monkeypatch.setattr(dbfcols, "_HEAD_MAX", 100)
    p = _write(tmp_path, "h.dbf", _dbf(FIELDS, ROWS))
    assert dbfcols.dbfread_columns(p, ("NAME", "FORMAT"), fallback=False) is None
    assert dbfcols.dbfread_columns(p, ("NAME", "FORMAT")) == _dbfread_rows(p, ("NAME", "FORMAT"))


def test_project_readers_equal_the_old_ones_on_synthetic_projects(tmp_path, reading):
    d = tmp_path / "Proj"
    d.mkdir()
    vfields = [
        (b"NAME", "C", 12),
        (b"TYPE", "C", 8),
        (b"UNIT", "C", 6),
        (b"ADDR", "C", 8),
        (b"ENG_UNITS", "C", 4),
        (b"COMMENT", "C", 16),
        (b"CLUSTER", "C", 6),
        (b"EQUIP", "C", 8),
        (b"ITEM", "C", 6),
        (b"FORMAT", "C", 8),
        (b"ENG_ZERO", "C", 6),
        (b"ENG_FULL", "C", 6),
    ]
    vrows = [
        (
            b"PV1",
            b"REAL",
            b"U1",
            b"D1",
            b"degC",
            b"\xa0first\x00",
            b"C1",
            b"Pump1",
            b"PV",
            b"###.#",
            b"0",
            b"100",
        ),
        (b"Dup", b"INT", b"", b"", b"", b"one", b"", b"", b"", b"#", b"", b""),
        (b"dup", b"INT", b"", b"", b"", b"two", b"", b"Eq", b"It", b"##", b"-5", b" 5 "),
        (b"", b"REAL", b"", b"", b"", b"nameless", b"", b"", b"", b"", b"", b""),
    ]
    (d / "variable.dbf").write_bytes(_dbf(vfields, vrows, flags=[b" ", b" ", b" ", b" "]))
    lfields = [
        (b"NAME", "C", 10),
        (b"TYPE", "C", 6),
        (b"COMMENT", "C", 10),
        (b"ZERO", "C", 4),
        (b"FULL", "C", 4),
        (b"ENG_UNITS", "C", 4),
    ]
    (d / "locvar.dbf").write_bytes(_dbf(lfields, [(b"LV1", b"INT", b"lv", b"0", b"9", b"m")]))
    proj = PJ.Project(str(d))
    path = str(d / "variable.dbf")
    assert _tag_values(proj.variable_tags()) == _reference_variable_tags(path)
    assert dict(proj.variable_formats()) == {t.name.lower(): t.format for t in proj.variable_tags()}
    idx = PJ.tag_index(str(d))
    ref = []
    items = []
    for r in _reference_read_dbf_rows(path):
        if not r.get("NAME", ""):
            continue
        ref.append(
            ("variable", r["NAME"], r["TYPE"], r["COMMENT"], r["ENG_ZERO"], r["ENG_FULL"], r["ENG_UNITS"])
        )
        if r["EQUIP"] and r["ITEM"]:
            items.append(
                (
                    "equipment",
                    "%s.%s" % (r["EQUIP"], r["ITEM"]),
                    r["TYPE"],
                    "Equipment item (tag %s)" % r["NAME"],
                    r["ENG_ZERO"],
                    r["ENG_FULL"],
                    r["ENG_UNITS"],
                )
            )
    ref.append(("local", "LV1", "INT", "lv", "0", "9", "m"))
    got = [
        (e.kind, e.name, e.type, e.comment, e.eng_zero, e.eng_full, e.eng_units)
        for e in idx.entries
        if e.project == "Proj"
    ]
    assert got == ref + items


# --------------------------------------------------------------------------- the cache
def test_one_cache_for_several_projects_follows_the_file(tmp_path):
    projs = []
    for k in range(3):
        d = tmp_path / ("P%d" % k)
        d.mkdir()
        (d / "variable.dbf").write_bytes(_dbf(FIELDS, [(b"T%d" % k, b"INT", b"", b"#")]))
        projs.append(PJ.Project(str(d)))
    first = projs[0].variable_tags()
    for p in projs[1:]:
        p.variable_tags()
    assert projs[0].variable_tags() is first  # another project did not evict it
    assert PJ.Project(str(tmp_path / "P0")).variable_tags() is first
    # a changed file is read again
    f = tmp_path / "P0" / "variable.dbf"
    f.write_bytes(_dbf(FIELDS, [(b"NEW", b"INT", b"", b"##"), (b"T0", b"INT", b"", b"#")]))
    st = os.stat(f)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    again = projs[0].variable_tags()
    assert [t.name for t in again] == ["NEW", "T0"]
    assert dict(projs[0].variable_formats()) == {"new": "##", "t0": "#"}


def test_cache_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(dbfcols, "MAX_ENTRIES", 2)
    paths = []
    for k in range(4):
        paths.append(_write(tmp_path, "t%d.dbf" % k, _dbf(FIELDS, ROWS)))
    for p in paths:
        dbfcols.rows(p)
    assert len(dbfcols._CACHE) == 2
    assert dbfcols.rows(paths[-1]) is dbfcols.rows(paths[-1])


@pytest.mark.parametrize("project", ["FxMain", "FxSim", "FxSa"])
def test_fixture_variable_tables(project, fx_install):
    """The fake install's variable tables (FxMain: 122 tags with equipment, items and
    formats; FxSim: 60; FxSa's) read alike (the corpus sweep is in tests_corpus/)."""
    pdir = fx_install.project(project)
    path = fx_install.file(project, "variable.DBF")
    proj = PJ.Project(pdir)
    tags = proj.variable_tags()
    assert tags
    assert _tag_values(tags) == _reference_variable_tags(path)
    assert proj.variable_tags() is tags
    assert dict(proj.variable_formats()) == {t.name.lower(): t.format for t in tags}
    ref = _reference_read_dbf_rows(path)
    assert dbfcols.citect_rows(path, PJ._INDEX_VARIABLE) == [
        tuple(r.get(c, "") for c in PJ._INDEX_VARIABLE) for r in ref
    ]


# --------------------------------------------------------------------------- _fresh_record
def _open_copy(src):
    """``src`` (a page of the fx_install copy, which resolves its own libraries) opened."""
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(str(src))


def _full(doc):
    from ctgedit.format.page import parse_ctg_bytes

    return reader.derive_page(parse_ctg_bytes(doc.serialize()), doc.path, records_fresh=True)[0]


def test_fresh_record_memo_and_stash(large):
    from ctgedit.format.page import _parse_object, parse_ctg
    from ctgedit.format.writer import object_payload

    reader.clear_fresh_memo()
    objs = parse_ctg(large).page.objects
    assert objs[5].body_raw != objs[6].body_raw
    po = objs[5]
    a = reader._fresh_record(po, 5)
    assert a is not po and a.body_raw == po.body_raw and a.bags == po.bags
    assert reader._fresh_record(po, 7) is a  # the same bytes: the same parse
    twin = objs[6]
    assert reader._fresh_record(twin, 6) is not a
    # a record's own stash of its present payload is used, a stale one is not
    reader.clear_fresh_memo()
    payload = object_payload(po)
    stash = _parse_object(5, 0, len(payload), 0, payload)
    po._fresh_parse = (payload, stash)
    assert reader._fresh_record(po, 5) is stash
    # ... and remembered: a copy of the record (an undo / redo's) finds it without a stash
    copy = _parse_object(5, 0, len(payload), 0, payload)
    assert reader._fresh_record(copy, 5) is stash
    reader.clear_fresh_memo()
    po._fresh_parse = (payload + b"x", stash)
    got = reader._fresh_record(po, 5)
    assert got is not stash and got.bags == stash.bags


def test_fresh_memo_bound_counts_what_the_parses_keep_alive(monkeypatch, large):
    """The memo's size is the memory its entries hold, and the bound holds: a page's re-parses
    traced by tracemalloc never exceed what the memo counts, and a small bound evicts down to it
    with the running size = the sum of the kept entries' costs."""
    import tracemalloc
    from ctgedit.format.page import parse_ctg

    objs = parse_ctg(large).page.objects
    sigs = [reader._record_sig(po) for po in objs]
    reader.clear_fresh_memo()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for i, po in enumerate(objs):
            reader._fresh_record(po, i, sigs[i])
        traced = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert len(reader._FRESH_MEMO) == len(set(sigs))
    assert reader._fresh_memo_size[0] >= traced
    assert reader._fresh_memo_size[0] == sum(c for _p, c in reader._FRESH_MEMO.values())
    reader.clear_fresh_memo()
    monkeypatch.setattr(reader, "_FRESH_MEMO_BYTES", 400_000)
    for i, po in enumerate(objs):
        reader._fresh_record(po, i, sigs[i])
    assert 0 < reader._fresh_memo_size[0] <= 400_000
    assert reader._fresh_memo_size[0] == sum(c for _p, c in reader._FRESH_MEMO.values())
    assert len(reader._FRESH_MEMO) < len(objs)
    reader.clear_fresh_memo()


def test_undo_redo_reuse_parses_and_equal_a_full_derive(monkeypatch, large):
    reader.clear_fresh_memo()
    doc = _open_copy(large)
    live = {id(r) for r in doc.parsed.page.objects}
    objs = doc.page.objects
    g = max(
        (i for i, o in enumerate(objs) if o.parent is None and o.members and not o.raw.get("_from_template")),
        key=lambda i: len(doc.page.subtree(i)),
    )
    n = len(doc.page.subtree(g))
    doc.move_objects([g], 3, 1)
    doc._rerender_page()
    assert reader.model_difference(doc.page, _full(doc)) == ""
    doc.undo()
    doc._rerender_page()
    assert reader.model_difference(doc.page, _full(doc)) == ""
    parsed = []
    real = reader._parse_object
    monkeypatch.setattr(reader, "_parse_object", lambda *a: parsed.append(1) or real(*a))
    doc.redo()
    doc._rerender_page()
    doc.undo()
    doc._rerender_page()
    assert len(parsed) < n  # the unit's records came from the memo
    assert reader.model_difference(doc.page, _full(doc)) == ""
    # the memo never holds a record of the document
    ids = live | {id(r) for r in doc.parsed.page.objects}
    assert not any(id(v[0]) in ids for v in reader._FRESH_MEMO.values())


# --------------------------------------------------------------------------- template shift
def _insert_at_front(doc):
    i = doc.create_object("rectangle", rect=(20, 20, 60, 50))
    po = doc.parsed.page.objects[i]
    doc.delete_objects([i])
    doc.add_object(po, 0)


def test_a_shifted_template_keeps_its_unit(large):
    doc = _open_copy(large)
    tpl = next(i for i, o in enumerate(doc.page.objects) if o.raw.get("_from_template"))
    _insert_at_front(doc)
    der = doc._derivation
    assert not der.full
    assert doc.page.objects[tpl + 1].raw.get("_from_template")
    assert reader.model_difference(doc.page, _full(doc)) == ""
    doc.undo()
    assert not doc._derivation.full
    assert reader.model_difference(doc.page, _full(doc)) == ""


def test_template_stored_last_shifts_without_a_full_derive(fx_page):
    """A page that stores its template LAST, after a genie (FxMain's FxTplLast: the genie
    FxBeforeTpl of eight members, then normal@XGA): a record put in front, and a genie member
    deleted, shift it."""
    doc = _open_copy(fx_page("FxTplLast"))
    tpl = [i for i, o in enumerate(doc.page.objects) if o.raw.get("_from_template")]
    assert tpl and tpl[0] > 0  # the template is not first
    _insert_at_front(doc)
    assert not doc._derivation.full
    objs = doc.page.objects
    g = next(
        i
        for i, o in enumerate(objs)
        if o.parent is None and len(o.members) >= 2 and doc.parsed.page.objects[i].subtype_size & 0x200
    )
    leaf = next(m for m in objs[g].members if not objs[m].members)
    doc.delete_objects([leaf])
    assert not doc._derivation.full
    assert reader.model_difference(doc.page, _full(doc)) == ""


# --------------------------------------------------------------------------- imports
def test_per_record_functions_import_nothing_at_call_time():
    src = open(reader.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    hot = {
        "_object_from",
        "_fresh_record",
        "_read_geometry",
        "_decode_style",
        "_apply_text_style",
        "_apply_animations",
        "_apply_button_style",
        "_literal_display",
        "_apply_symbol_set",
        "_attach_embedded_bitmap",
        "_attach_metafile",
        "_expand_symbol",
        "_cicode_shows_no_symbol",
        "_classify_container",
        "_state0_pair",
        "_member_nodes",
        "_own_text_caption",
        "_graft_template_member_colours",
        "_repair_stripped_nodes",
        "_build_object_forest",
        "_derive_objects",
        "derive_page",
        "_derive_context",
    }
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in hot:
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    found.setdefault(node.name, []).append(sub.lineno)
    assert found == {}


def test_reader_import_stays_qt_free():
    code = "import sys; import ctgedit.io.reader; print('PySide6' in sys.modules)"
    env = dict(os.environ)
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert out.stdout.strip() == "False", out.stderr
