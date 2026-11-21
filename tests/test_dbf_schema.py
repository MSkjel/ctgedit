"""Project table layouts."""

import importlib.util
import json
import os

import pytest

import fxinstall

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOL = os.path.join(REPO, "tools", "live", "dbf_schema.py")


def _tool():
    spec = importlib.util.spec_from_file_location("dbf_schema_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture_tables():
    """[(project, path, schema table)] of every table of the committed fake install."""
    from ctgedit.format import dbfschema as S
    from ctgedit.format.projects import read_dbf_rows

    user = os.path.join(fxinstall.FX_INSTALL, "User")
    out = [("", os.path.join(user, "MASTER.DBF"), S.MASTER)]
    for proj in sorted(os.listdir(user)):
        folder = os.path.join(user, proj)
        if not os.path.isdir(folder):
            continue
        files = {
            os.path.splitext(f)[0].lower(): os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(".dbf")
        }
        languages = set()
        if "lang" in files:
            languages = {
                os.path.splitext(r.get("SRC_FILE", ""))[0].lower() for r in read_dbf_rows(files["lang"])
            }
        for stem, path in sorted(files.items()):
            out.append((proj, path, S.LANGUAGE if stem in languages else stem))
    return out


# --------------------------------------------------------------------------- the fixtures
def test_every_fixture_table_has_the_schema_layout():
    """Field for field (name as spelled, type, size, decimals, order): what CtDraw checks
    the tables against."""
    from ctgedit.format import dbfschema as S

    known = set(S.tables())
    bad, seen = [], 0
    for proj, path, table in _fixture_tables():
        rel = os.path.relpath(path, fxinstall.FX_INSTALL)
        if table not in known:
            bad.append("%s: no table of the schema is called %s" % (rel, table))
            continue
        have = S.read_fields(path)
        want = S.fields(table)
        if have != want:
            bad.append(
                "%s: %s" % (rel, "; ".join(S.compare(have, want, table, order=True)) or "the fields differ")
            )
        seen += 1
    assert not bad, "\n".join(bad)
    assert seen >= 170


def test_every_indexed_fixture_table_has_its_index():
    """A table whose form says INDEX or WRITEINDEXED (almprior, almmodes, contype, variable,
    locvar, the page register's) and a language table have ``<table>.NDX`` on the schema's
    field, its key as long as the field, an entry for every live row (as the shipped
    projects have them: the capture's tools read these tables)."""
    from ctgedit.format import dbf, dbfschema as S

    bad, seen = [], set()
    for proj, path, table in _fixture_tables():
        field = S.index_field(table) if table in S.tables() else ""
        if not field:
            continue
        rel = os.path.relpath(path, fxinstall.FX_INSTALL)
        width = next(w for n, _t, w, _d in S.fields(table) if n == field)
        problems = S.index_problems(path, field, width, rel)
        if problems:
            bad += problems
            continue
        ndx = os.path.splitext(path)[0] + ".NDX"
        with dbf.Dbf(path, writable=False) as t:
            live = [
                (r + 1, t.field(rec, field))
                for r, rec in ((r, t.read(r)) for r in range(t.nrec))
                if rec[:1] != b"*"
            ]
        with dbf.Ndx(ndx, writable=False) as x:
            have = {r for _k, r in x.entries()}
            if not {r for r, _v in live} <= have:
                bad.append("%s: the index misses rows" % rel)
            for r, v in live[:20]:
                if r not in x.find(v.strip()):
                    bad.append("%s: the index does not find row %d (%s)" % (rel, r, v))
        seen.add(table)
    assert not bad, "\n".join(bad)
    assert {"almprior", "almmodes", "contype", "variable", "locvar", "language", "pgdynobj"} <= seen


def test_the_fixture_tables_cover_the_kinds_the_app_reads():
    names = {t for _p, _f, t in _fixture_tables()}
    assert {
        "master",
        "language",
        "pages",
        "pgdynobj",
        "variable",
        "equip",
        "category",
        "pagemenu",
        "lang",
        "almprior",
        "almmodes",
    } <= names
    assert not names & {"almcat", "menu", "langtable"}


def test_the_page_register_writes_the_forms_fields_in_their_order():
    from ctgedit.format import dbfschema as S
    from ctgedit.io import pgregister as PR

    for table, fields in PR.TABLE_FIELDS.items():
        assert [f.upper() for f in fields] == [n.upper() for n, *_r in S.fields(table)], table
    assert S.fields("pglang") == [("NATIVE", "C", PR.LANG_MAX, 0)]


def test_the_committed_schema_holds_names_and_sizes_only():
    from ctgedit.format import dbfschema as S

    data = S.load()
    assert set(data) == {"about", "tables", "retired"}
    for name, ent in data["tables"].items():
        assert name == name.lower() and set(ent) <= {"form", "file", "fields", "index"}, name
        assert ent["fields"], name
        if "index" in ent:
            assert ent["index"] in [f[0] for f in ent["fields"]], name
        for f in ent["fields"]:
            n, t, w, d = f
            assert isinstance(n, str) and 0 < len(n) <= 10 and t in "CNLDM", (name, f)
            assert 0 < w <= 254 and d == 0, (name, f)
    # a few facts the rule rests on
    assert S.fields("lang")[0] == ("Language", "C", 64, 0)
    assert [n for n, *_r in S.fields("users")].count("PASSWORD") == 1
    assert "PORT" not in [n for n, *_r in S.fields("almsrv")]
    assert data["retired"]["almsrv"] == [["PORT", "C", 16, 0]]
    # the indexes (the forms' INDEX / WRITEINDEXED KEY; the language tables' NATIVE)
    assert {
        t: S.index_field(t)
        for t in ("almprior", "almmodes", "contype", "variable", "locvar", "pgdynobj", "language")
    } == {
        "almprior": "PRIORITY",
        "almmodes": "DISPNAME",
        "contype": "NAME",
        "variable": "NAME",
        "locvar": "NAME",
        "pgdynobj": "PAGE",
        "language": "NATIVE",
    }
    assert S.index_field("pages") == S.index_field("master") == S.index_field("equip") == ""


# --------------------------------------------------------------------------- the writer
def test_a_new_table_refuses_a_long_value_and_an_unknown_field(tmp_path):
    from ctgedit.format import dbfschema as S

    p = str(tmp_path / "include.DBF")
    with pytest.raises(S.FieldTooLong) as e:
        S.create_table(p, "include", [{"NAME": "FxLib", "COMMENT": "x" * 49}])
    assert "COMMENT" in str(e.value) and "49" in str(e.value) and "48" in str(e.value)
    with pytest.raises(ValueError, match="no field"):
        S.create_table(p, "include", [{"NAME": "FxLib", "TITLE": "x"}])
    assert not os.path.exists(p)
    S.create_table(p, "include", [{"name": "FxLib", "COMMENT": "x" * 48}], indexes=["NAME"])
    assert S.read_fields(p) == S.fields("include")
    assert os.path.exists(str(tmp_path / "include.NDX"))


def test_a_new_table_refuses_a_row_with_more_values_than_fields(tmp_path):
    """A sequence row longer than the layout is an error (its extra values were dropped)."""
    from ctgedit.format import dbfschema as S

    with pytest.raises(ValueError, match="3 values, the table has 2 fields"):
        S.table_bytes("include", [("FxLib", "c", "lost value")])
    p = str(tmp_path / "include.DBF")
    with pytest.raises(ValueError):
        S.create_table(p, "include", [("FxLib", "c", "lost value")])
    assert not os.path.exists(p)
    assert len(S.table_bytes("include", [("FxLib", "c")])) == 211


def test_a_new_table_gets_the_index_its_form_keeps(tmp_path):
    """By default the schema's index (an INDEX form's KEY, a language table's NATIVE), its
    key as long as the field; a NONINDEX form's table none; ``()`` none."""
    from ctgedit.format import dbf, dbfschema as S

    S.create_table(str(tmp_path / "almprior.DBF"), "almprior", [{"PRIORITY": "1"}])
    S.create_table(str(tmp_path / "fxish.DBF"), "language", [{"NATIVE": "Fx Start"}])
    S.create_table(str(tmp_path / "include.DBF"), "include", [{"NAME": "FxLib"}])
    S.create_table(str(tmp_path / "contype.DBF"), "contype", [], indexes=())
    assert S.ndx_key(str(tmp_path / "almprior.NDX")) == ("PRIORITY", 16)
    assert S.ndx_key(str(tmp_path / "fxish.NDX")) == ("NATIVE", 100)
    assert not os.path.exists(str(tmp_path / "include.NDX"))
    assert not os.path.exists(str(tmp_path / "contype.NDX"))
    with dbf.Ndx(str(tmp_path / "almprior.NDX"), writable=False) as x:
        assert x.find("1") == [1]
    assert S.index_problems(str(tmp_path / "almprior.DBF"), "PRIORITY", 16) == []
    assert S.index_problems(str(tmp_path / "contype.DBF"), "NAME", 20, "fx") == [
        "fx: the index on NAME (contype.NDX) is missing"
    ]
    assert S.index_problems(str(tmp_path / "almprior.DBF"), "DISPNAME", 254) == [
        "the index is on PRIORITY (key 16), not DISPNAME (254)"
    ]


def test_a_new_table_keeps_the_forms_spelling(tmp_path):
    from ctgedit.format import dbfschema as S
    from ctgedit.format.projects import read_dbf_rows

    p = str(tmp_path / "LANG.DBF")
    S.create_table(p, "lang", [{"LANGUAGE": "Fxish(Fxland)", "SRC_FILE": "fxish.dbf"}])
    assert [n for n, *_r in S.read_fields(p)] == ["Language", "ANSIToOEM", "COMMENT", "SRC_FILE"]
    assert read_dbf_rows(p)[0]["SRC_FILE"] == "fxish.dbf"


def test_compare_names_each_difference():
    from ctgedit.format import dbfschema as S

    want = [("NAME", "C", 16, 0), ("Language", "C", 64, 0), ("COMMENT", "C", 48, 0)]
    have = [("NAME", "C", 32, 0), ("COMMENT", "C", 48, 0), ("LANGUAGE", "C", 64, 0), ("PORT", "C", 16, 0)]
    lines = S.compare(have, want, "fx")
    assert lines == [
        "fx: the field NAME has size 32, not 16",
        "fx: the field PORT (size 16) is not in the layout",
    ]
    assert S.compare(have[:3], [want[0], want[2]], "fx", extra=False) == [
        "fx: the field NAME has size 32, not 16"
    ]
    same = [("NAME", "C", 16, 0), ("COMMENT", "C", 48, 0), ("LANGUAGE", "C", 64, 0)]
    assert S.compare(same, want) == []
    order = S.compare(same, want, order=True)
    assert order == ["the field Language is spelled LANGUAGE"]


# --------------------------------------------------------------------------- citect.frm
FAKE_FRM = r"""!****************************************************************
!* a fake citect.frm (our own forms, the real syntax)
!****************************************************************
COMMAND "Compile", "fxcmp", "/I", Master.NAME;

FORM "Fx Tags", "Fx Tags", "fxtags", WRITEINDEXED, KEY "NAME";

1:      "NAME",         79, readwrite_foreign;
2:      "TYPE",         16, readwrite_foreign, "Fx Types", "NAME";
4:      "COMMENT",     254, readwrite_foreign;
5:      "TYPE",         16, readwrite_foreign;
6:      "Mixed",         8, readonly;

NORMAL
"Name   {1        }
Comment {4        }"

FORM "Fx Page Things", "fxpgthing", INDEX, KEY "PAGE", "Pages", "NAME";

1:      "PAGE",                 64, readonly;
2:      "AN",                    6, readwrite_foreign, not_searchable;

NORMAL
""
"""


def test_the_frm_reader_takes_the_forms_and_their_fields():
    from ctgedit.format import dbfschema as S

    forms = S.parse_frm(FAKE_FRM)
    assert [(f.menu, f.title, f.table, f.index, f.index_key) for f in forms] == [
        ("Fx Tags", "Fx Tags", "fxtags", "WRITEINDEXED", "NAME"),
        ("Fx Page Things", "", "fxpgthing", "INDEX", "PAGE"),
    ]
    assert [S.form_index(f) for f in forms] == ["NAME", "PAGE"]
    tags = forms[0]
    assert [n for n, *_r in tags.lines] == [1, 2, 4, 5, 6]
    # a gap (3) is no field, a name listed again is the first one only
    assert S.form_fields(tags) == [
        ("NAME", "C", 79, 0),
        ("TYPE", "C", 16, 0),
        ("COMMENT", "C", 254, 0),
        ("Mixed", "C", 8, 0),
    ]
    assert S.form_fields(forms[1]) == [("PAGE", "C", 64, 0), ("AN", "C", 6, 0)]


def _fake_install(tmp_path):
    """Bin\\citect.frm, User\\MASTER.DBF and a shipped project (Example) with a table of a
    form, an older one with a retired field, a language table."""
    from ctgedit.format import dbf

    root = tmp_path / "FxRealInstall"
    (root / "Bin").mkdir(parents=True)
    (root / "Bin" / "citect.frm").write_bytes(FAKE_FRM.encode("cp1252"))
    user = root / "User"
    ex = user / "Example"
    ex.mkdir(parents=True)
    dbf.create_table(
        str(user / "MASTER.DBF"), [("NAME", 64), ("TITLE", 254), ("PATH", 240)], [{"NAME": "Example"}]
    )
    dbf.create_table(
        str(ex / "fxtags.DBF"),
        [("NAME", 79), ("COMMENT", 254), ("TYPE", 16), ("MIXED", 8), ("OLDPORT", 16)],
        indexes=["NAME"],
    )
    # (fxpgthing's form says INDEX, but this one has no .NDX: the tool says so)
    dbf.create_table(str(ex / "fxpgthing.DBF"), [("PAGE", 64), ("AN", 6)])
    dbf.create_table(
        str(ex / "lang.DBF"),
        [("Language", 64), ("SRC_FILE", 64)],
        [{"LANGUAGE": "Fxish", "SRC_FILE": "fxish.dbf"}],
    )
    dbf.create_table(str(ex / "fxish.DBF"), [("NATIVE", 100), ("LOCAL", 100)], indexes=["NATIVE"])
    # a project that is not a shipped one is never read
    (user / "FxUserProject").mkdir()
    dbf.create_table(str(user / "FxUserProject" / "fxtags.DBF"), [("NAME", 5)])
    return root


def test_the_tool_writes_the_schema_and_checks_it(tmp_path, capsys):
    tool = _tool()
    root = _fake_install(tmp_path)
    frm, user = str(root / "Bin" / "citect.frm"), str(root / "User")
    out = str(tmp_path / "schema.json")
    assert tool.main(["--frm", frm, "--user", user, "--out", out]) == 0
    data = json.load(open(out, encoding="utf-8"))
    t = data["tables"]
    assert set(t) == {"fxtags", "fxpgthing", "master", "language"}
    assert t["fxtags"] == {
        "form": "Fx Tags",
        "index": "NAME",
        "fields": [
            ["NAME", "C", 79, 0],
            ["TYPE", "C", 16, 0],
            ["COMMENT", "C", 254, 0],
            ["Mixed", "C", 8, 0],
        ],
    }
    assert t["fxpgthing"]["index"] == "PAGE" and t["language"]["index"] == "NATIVE"
    assert "index" not in t["master"]
    assert t["master"]["fields"][2] == ["PATH", "C", 240, 0]
    assert t["language"]["fields"] == [["NATIVE", "C", 100, 0], ["LOCAL", "C", 100, 0]]
    assert data["retired"] == {"fxtags": [["OLDPORT", "C", 16, 0]]}
    noted = capsys.readouterr().out
    assert "OLDPORT" in noted and "FxUserProject" not in noted
    assert "Example\\fxpgthing.DBF: the index on PAGE (fxpgthing.NDX) is missing" in noted
    assert "fxtags.DBF: the index" not in noted
    from ctgedit.format import dbfschema as S

    assert S.fields("fxtags", out)[3] == ("Mixed", "C", 8, 0)
    assert tool.main(["--frm", frm, "--user", user, "--out", out, "--check"]) == 0
    assert "matches" in capsys.readouterr().out
    # a size changed in the install: --check says which field
    text = FAKE_FRM.replace('"AN",                    6', '"AN",                   16')
    (root / "Bin" / "citect.frm").write_bytes(text.encode("cp1252"))
    assert tool.main(["--frm", frm, "--user", user, "--out", out, "--check", "--quiet"]) == 1
    said = capsys.readouterr().out
    assert "fxpgthing: the field AN has size 6, not 16" in said
    # an index the form no longer keeps: --check says so
    (root / "Bin" / "citect.frm").write_bytes(
        FAKE_FRM.replace('INDEX, KEY "PAGE"', "NONINDEX").encode("cp1252")
    )
    assert tool.main(["--frm", frm, "--user", user, "--out", out, "--check", "--quiet"]) == 1
    assert "fxpgthing: the index is on PAGE, not no field" in capsys.readouterr().out
