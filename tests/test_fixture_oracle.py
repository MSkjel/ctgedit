"""The install changes made by the CtDraw capture tool, on a fake install."""

import hashlib
import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "live", "fixture_oracle.py")

#: MASTER.DBF as Plant SCADA Studio writes it (the real install's columns)
MASTER_FIELDS = [
    ("NAME", 64),
    ("TITLE", 254),
    ("PATH", 240),
    ("MAJOR_VER", 4),
    ("MINOR_VER", 4),
    ("DATE", 20),
    ("TIME", 20),
    ("PRIVILEGE", 32),
    ("STATUS", 10),
]


@pytest.fixture(scope="module")
def fo():
    spec = importlib.util.spec_from_file_location("fixture_oracle", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fixture_oracle"] = mod  # (its dataclasses look their module up)
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop("fixture_oracle", None)


def _frm_text(sizes=None, keys=None):
    """A fake Bin\\citect.frm: a FORM per table of the committed schema with its fields
    and its index KEY (``sizes`` = {(table, FIELD): size} changes some sizes, ``keys`` =
    {table: FIELD or ''} some keys)."""
    from ctgedit.format import dbfschema as S

    sizes, keys = sizes or {}, keys or {}
    out = ["! a fake citect.frm (the committed schema's forms)"]
    for table in S.tables():
        ent = S.load()["tables"][table]
        if "form" not in ent:
            continue
        key = keys.get(table, ent.get("index", ""))
        mode = 'INDEX, KEY "%s"' % key if key else "NONINDEX"
        out.append('FORM "%s", "%s", %s;' % (ent["form"], table, mode))
        for i, (n, _t, w, _d) in enumerate(S.fields(table), 1):
            out.append('%d: "%s", %d, readwrite;' % (i, n, sizes.get((table, n.upper()), w)))
        out += ["NORMAL", '""']
    return "\r\n".join(out) + "\r\n"


def _install(tmp_path, frm=True):
    """A fake install laid out like the real one, with one project of the user's (and a
    Bin\\citect.frm of the committed schema's forms)."""
    from ctgedit.format import dbf

    root = tmp_path / "FxRealInstall"
    user = root / "User"
    (user / "FxUserProject").mkdir(parents=True)
    if frm:
        (root / "Bin").mkdir()
        (root / "Bin" / "citect.frm").write_bytes(_frm_text().encode("cp1252"))
    (user / "FxUserProject" / "FxUserPage.ctg").write_bytes(b"fx page bytes")
    (root / "Config").mkdir()
    (root / "Config" / "citect.ini").write_bytes(
        b"[CtEdit]\r\nUSER=" + str(user).encode() + b"\r\nLastDatabase=FxUserProject\r\n"
        b"\r\n[Animator]\r\nFastDisplay=0\r\n"
    )
    (root / "Config" / "other.xml").write_bytes(b"<fx/>")
    (root / "Logs").mkdir()
    (root / "Logs" / "errs").write_bytes(b"the user's last compile\r\n")
    dbf.create_table(
        str(user / "MASTER.DBF"),
        MASTER_FIELDS,
        [
            {
                "NAME": "FxUserProject",
                "PATH": str(user / "FxUserProject") + "\\",
                "MINOR_VER": "0014",
                "DATE": "29 Sep 2026",
                "TIME": "23:30",
            }
        ],
    )
    return root


def _tree(root):
    out = {}
    for d, subs, files in os.walk(root):
        for s in subs:
            out[os.path.relpath(os.path.join(d, s), root)] = "folder"
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha1(fh.read()).hexdigest()
    return out


def _change(fo, tmp_path, root, name="run1", log=None):
    return fo.InstallChange(
        fo.locate(str(root)),
        str(tmp_path / "runs" / name),
        processes=dict,
        log=log if log is not None else (lambda _t: None),
    )


def _include_names(path):
    from ctgedit.format.projects import included_project_names

    return included_project_names(path)


def test_the_change_and_its_restore_leave_the_install_as_it_was(fo, tmp_path):
    from ctgedit.format.projects import read_dbf_rows

    root = _install(tmp_path)
    before = _tree(root)
    lines = []
    ch = _change(fo, tmp_path, root, log=lines.append)
    ch.apply()
    user = root / "User"
    # the copies, each with this run's marker
    for target in fo.TARGETS:
        assert (user / target / fo.MARKER).read_bytes().decode() == ch.state["run_id"]
    src = os.path.join(fo.FIXTURES, "User")
    assert (user / "FxMain" / "FxShapes.ctg").read_bytes() == open(
        os.path.join(src, "FxMain", "FxShapes.ctg"), "rb"
    ).read()
    assert (user / "FxInclude" / "cicode.ctl").is_file()
    assert (user / "FxSystem" / "fxsys.frm").is_file()
    # the rows: MASTER.DBF names every copy at its folder; the includes end with ours
    rows = read_dbf_rows(str(user / "MASTER.DBF"))
    assert [r["NAME"] for r in rows] == ["FxUserProject"] + list(fo.TARGETS)
    assert {r["NAME"]: r["PATH"] for r in rows}["FxMain"] == str(user / "FxMain") + "\\"
    assert _include_names(str(user / "FxMain")) == ["FxLib", "FxStyle", "FxInclude", "FxSystem"]
    assert _include_names(str(user / "FxLib")) == ["FxInclude", "FxSystem"]
    assert _include_names(str(user / "FxSa")) == ["FxLib", "FxStyle", "FxInclude", "FxSystem"]
    assert _include_names(str(user / "FxInclude")) == []
    assert _include_names(os.path.join(src, "FxMain")) == ["FxLib", "FxStyle"]
    ini = (root / "Config" / "citect.ini").read_bytes()
    assert fo.ini_get(ini, "CtEdit", "LastDatabase") == "FxMain"
    assert fo.ini_get(ini, "Animator", "FastDisplay") == "1"
    # the backup is outside the install
    assert not str(ch.run_dir).startswith(str(root))
    assert len(os.listdir(os.path.join(ch.run_dir, "backup"))) == 3
    # what a run does besides: Studio writes an index, the compiler its log, the ini again
    (user / "MASTER.NDX").write_bytes(b"an index Studio made")
    (root / "Logs" / "errs").write_bytes(b"the capture's compile\r\n")
    (root / "Config" / "citect.ini").write_bytes(ini + b"[Fx]\r\nMore=1\r\n")
    (user / "FxMain" / "FxShapes.ctF").write_bytes(b"made by CtDraw")
    ch.note_seen()  # (as cmd_run does before its restore)
    assert ch.restore()
    assert _tree(root) == before
    assert ch.state["phase"] == "restored"
    assert any(t.startswith("Restore verified") for t in lines)
    assert fo.open_runs(str(tmp_path / "runs")) == []


@pytest.mark.parametrize("where", ["folder", "master"])
def test_a_name_in_the_install_already_refuses_the_change(fo, tmp_path, where):
    from ctgedit.format import dbf

    root = _install(tmp_path)
    if where == "folder":
        (root / "User" / "FxLib").mkdir()
    else:
        with dbf.Dbf(str(root / "User" / "MASTER.DBF")) as t:
            t.append([t.encode({"NAME": "fxmain", "PATH": "D:\\FxElsewhere\\fxmain\\"})])
    before = _tree(root)
    ch = _change(fo, tmp_path, root)
    with pytest.raises(fo.Refused, match="FxLib" if where == "folder" else "FxMain"):
        ch.apply()
    assert _tree(root) == before
    assert not os.path.exists(ch.run_dir)


def test_a_running_studio_refuses_the_change(fo, tmp_path):
    root = _install(tmp_path)
    ch = fo.InstallChange(
        fo.locate(str(root)),
        str(tmp_path / "runs" / "r"),
        processes=lambda: {"CitectIDE.exe": [1234]},
        log=lambda _t: None,
    )
    with pytest.raises(fo.Refused, match="CitectIDE"):
        ch.apply()
    assert not os.path.exists(ch.run_dir)


def test_the_restore_command_after_a_crash_in_the_copies(fo, tmp_path, monkeypatch, capsys):
    """The process dies half way through the copies: the restore command (a new process,
    only the state file) deletes what was made and puts the backups back."""
    root = _install(tmp_path)
    before = _tree(root)
    real_copy = fo.copy_project
    copied = []

    def dying_copy(src, dst):
        real_copy(src, dst)
        if len(copied) == 3:
            raise KeyboardInterrupt("the process is killed")
        copied.append(dst)

    monkeypatch.setattr(fo, "copy_project", dying_copy)
    ch = _change(fo, tmp_path, root)
    with pytest.raises(KeyboardInterrupt):
        ch.apply()
    monkeypatch.setattr(fo, "copy_project", real_copy)
    assert (root / "User" / "FxStyle").is_dir() and not (root / "User" / "FxMain").exists()
    assert fo.open_runs(str(tmp_path / "runs")) == [ch.run_dir]
    monkeypatch.setattr(fo, "running_programs", dict)
    assert fo.main(["restore", "--backup-root", str(tmp_path / "runs")]) == 0
    assert _tree(root) == before
    assert "Restore verified" in capsys.readouterr().out
    assert fo.open_runs(str(tmp_path / "runs")) == []
    assert fo.main(["restore", "--backup", ch.run_dir]) == 0  # (again: a no-op)


def test_the_restore_keeps_a_folder_without_its_marker(fo, tmp_path):
    root = _install(tmp_path)
    master = (root / "User" / "MASTER.DBF").read_bytes()
    ch = _change(fo, tmp_path, root)
    ch.apply()
    (root / "User" / "FxSim" / fo.MARKER).write_bytes(b"another run")
    assert not ch.restore()
    assert (root / "User" / "FxSim").is_dir()  # (not ours to delete)
    assert not (root / "User" / "FxMain").exists()
    assert (root / "User" / "MASTER.DBF").read_bytes() == master
    assert ch.state["phase"] != "restored"


def test_the_dry_run_changes_nothing(fo, tmp_path, monkeypatch, capsys):
    root = _install(tmp_path)
    before = _tree(root)
    monkeypatch.setenv("CTG_SCADA_ROOT", "none")  # (cmd_run sets it for the run)
    runs = tmp_path / "runs"
    assert (
        fo.main(
            [
                "run",
                "--dry-run",
                "--install-root",
                str(root),
                "--backup-root",
                str(runs),
                "--steps",
                "turns,pages:FxMain/FxShapes",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert _tree(root) == before and not runs.exists()
    assert "Append to MASTER.DBF: NAME FxMain" in out
    assert "Set citect.ini [Animator] FastDisplay=1" in out
    assert "pages:FxMain/FxShapes" in out and "turns:T01_point_turn" in out
    assert "pages:FxMain/FxGenies" not in out
    assert "citect.frm: all match" in out


def test_a_table_the_forms_do_not_match_refuses_the_change(fo, tmp_path):
    """The preflight: CtDraw stops on a table whose field size is not its form's ('Form:
    Dynamic Object ... database field size mismatch'), so the run refuses before it
    changes anything, naming the table, the field and both sizes."""
    root = _install(tmp_path)
    (root / "Bin" / "citect.frm").write_bytes(
        _frm_text({("pgdynobj", "AN"): 16, ("include", "COMMENT"): 128}).encode("cp1252")
    )
    before = _tree(root)
    ch = _change(fo, tmp_path, root)
    with pytest.raises(fo.Refused) as e:
        ch.apply()
    said = str(e.value)
    assert "FxMain\\pgdynobj.DBF: the field AN has size 6, not 16" in said
    assert "FxInclude\\include.DBF: the field COMMENT has size 48, not 128" in said
    assert "dbf_schema.py --check" in said and "Nothing was changed" in said
    assert _tree(root) == before
    assert not os.path.exists(ch.run_dir)


def test_an_install_without_its_forms_refuses_the_change(fo, tmp_path):
    root = _install(tmp_path, frm=False)
    before = _tree(root)
    ch = _change(fo, tmp_path, root)
    with pytest.raises(fo.Refused, match="citect.frm"):
        ch.apply()
    assert _tree(root) == before


def test_the_table_check_reads_each_kind_of_table(fo, tmp_path):
    """A form's table (a missing field, another size, a field the form has not), a
    language table (lang.DBF SRC_FILE), a table no form names (not checked)."""
    from ctgedit.format import dbf, dbfschema as S

    frm = tmp_path / "citect.frm"
    frm.write_bytes(_frm_text().encode("cp1252"))
    proj = tmp_path / "src" / "User" / "FxOne"
    proj.mkdir(parents=True)
    dbf.create_table(str(proj / "include.DBF"), [("NAME", 64), ("COMMENT", 40), ("FX", 4)])
    dbf.create_table(str(proj / "cluster.DBF"), [("NAME", 16)])
    S.create_table(str(proj / "LANG.DBF"), "lang", [{"LANGUAGE": "Fxish", "SRC_FILE": "fxish.dbf"}])
    dbf.create_table(str(proj / "fxish.DBF"), [("NATIVE", 100), ("LOCAL", 254)])
    dbf.create_table(str(proj / "fxown.DBF"), [("WHATEVER", 3)])
    # INDEX forms: almprior without its .NDX, almmodes keyed on another field, contype right
    S.create_table(str(proj / "almprior.DBF"), "almprior", [{"PRIORITY": "1"}], indexes=())
    S.create_table(str(proj / "almmodes.DBF"), "almmodes", [], indexes=["SHORTNAME"])
    S.create_table(str(proj / "contype.DBF"), "contype", [{"NAME": "FxL1"}])
    bad = fo.table_problems(str(tmp_path / "src" / "User"), str(frm), [("FxOne", "FxTwo")])
    assert bad == [
        "FxTwo\\almmodes.DBF: the index is on SHORTNAME (key 48), not DISPNAME (100)",
        "FxTwo\\almprior.DBF: the index on PRIORITY (almprior.NDX) is missing",
        "FxTwo\\cluster.DBF: the field COMMENT (size 48) is missing",
        "FxTwo\\fxish.DBF: the field LOCAL has size 254, not 100",
        "FxTwo\\fxish.DBF: the index on NATIVE (fxish.NDX) is missing",
        "FxTwo\\include.DBF: the field COMMENT has size 40, not 48",
        "FxTwo\\include.DBF: the field FX (size 4) is not in the layout",
    ]
    # a form whose KEY is not the committed schema's: the schema is stale
    frm.write_bytes(_frm_text(keys={"contype": "COMMENT"}).encode("cp1252"))
    bad = fo.table_problems(str(tmp_path / "src" / "User"), str(frm), [("FxOne", "FxTwo")])
    assert "FxTwo\\contype.DBF: the index is on NAME (key 20), not COMMENT (128)" in bad
    assert any("committed schema's contype" in b for b in bad)


def test_the_committed_fixtures_pass_the_table_check(fo, tmp_path):
    frm = tmp_path / "citect.frm"
    frm.write_bytes(_frm_text().encode("cp1252"))
    assert fo.table_problems(os.path.join(fo.FIXTURES, "User"), str(frm)) == []


def test_list_names_every_step_and_its_tests(fo, capsys):
    assert fo.main(["--list"]) == 0
    out = capsys.readouterr().out
    heads = [ln.split(" (")[0] for ln in out.splitlines() if not ln.startswith(" ")]
    assert heads == list(fo.STEPS) + ["Not captured, on purpose"]
    assert "tests/test_turn_memory.py" in out
    for test, needs in fo.OUT_OF_SCOPE.items():
        for need, _why in needs:
            assert "  %s: %s" % (test, need) in out
    for unit in (
        "newpage:cut_link",
        "place:grown_element",
        "elements:append",
        "draw:copy_paste_named",
        "pages:FxSa/FxSaMaster",
    ):
        assert fo.select_units(unit), unit


def test_steps_select_units_by_pattern(fo):
    units = fo.select_units("turns:T2*,compile")
    assert [u.key for u in units] == [
        "turns:T20_gradient_mirror",
        "turns:T21_composite_turn",
        "turns:T22_composite_mirror",
        "turns:T23_text_turn",
        "turns:T24_cicode_point_turn",
        "turns:T25_group_symbol_turn",
        "turns:T26_point_text_two_turns",
        "turns:T27_point_turn_reopen_turn",
        "turns:T28_point_text_turn_reopen_turn",
        "turns:T29_point_text_mirror_save_two_turns",
        "compile:FxMain",
        "compile:FxSim",
    ]
    with pytest.raises(fo.Refused):
        fo.select_units("nonesuch")


def test_prepare_maps_names_to_ans_and_makes_the_capture_pages(fo, tmp_path):
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg

    got = fo.run_prepare(str(tmp_path / "prep"), log=lambda _t: None)
    arrange = got["names"]["FxMain/FxArrange"]["names"]
    assert arrange["FxPointGenie"]["root"] and not arrange["FxLeafBefore"]["root"]
    assert arrange["FxPointGenie"]["an"] == arrange["FxPointGenie"]["handle"] + 1
    for tid, page, names, _ops, _feeds in fo.TURNS:
        for n in names:
            assert got["names"]["FxMain/" + page]["names"][n]["root"], (page, n)
        if "_text_" in tid:  # (a text turn turns a text)
            assert TC_TEXT in [got["names"]["FxMain/" + page]["names"][n]["type_code"] for n in names], tid
    assert got["names"]["FxMain/FxMany"]["names"]["FxManyPump1"]["root"]
    # the cut_link page's template root (fx_style normal@XGA, AN 11) and the pump it keeps
    tpl = got["templates"]["FxMain/" + fo.CUT_LINK_PAGE]
    assert tpl["an"] == tpl["handle"] + 1 == 11
    assert tpl["reference"].lower() == "fx_style.normal@xga.<ctt"
    assert tpl["keep"] == ["fxgenie.fx_pump.<ctm"]
    gaps = parse_ctg(str(tmp_path / "prep" / "inputs" / "FxOracleGaps.ctg"))
    assert [G.get_handle(po) for po in gaps.page.objects] == [0, 1, 2, 4, 6, 13]
    pies = parse_ctg(str(tmp_path / "prep" / "inputs" / "FxOraclePies.ctg"))
    assert [G.parse_shape_tail(po)["start"] for po in pies.page.objects] == list(fo.PIE_ANGLES)
    again = fo.run_prepare(str(tmp_path / "prep2"), log=lambda _t: None)
    assert again["inputs"] == got["inputs"]  # (a fixed identity)


#: a text record's type code (format/codes.TC.TEXT)
TC_TEXT = 0x05


def test_table_rows_and_ini_lines(fo):
    from ctgedit.format import dbf

    old = dbf.table_bytes([("NAME", 8), ("CMD", 12)], [{"NAME": "FxA", "CMD": "Beep()"}])
    new = dbf.table_bytes([("NAME", 8), ("CMD", 12)], [{"NAME": "FxA", "CMD": "WinFree()"}, {"NAME": "FxB"}])
    d = fo.dbf_diff(old, new)
    assert d["fields"] == ["NAME", "CMD"]
    assert d["added"] == [{"NAME": "FxB", "_flag": " "}]
    assert d["changed"] == [{"index": 0, "flag": [" ", " "], "fields": {"CMD": ["Beep()", "WinFree()"]}}]
    raw = b"[CtEdit]\r\nlastdatabase = FxOld\r\n\r\n[Page]\r\nScanTime=250\r\n"
    out = fo.ini_set(fo.ini_set(raw, "CTEDIT", "LastDatabase", "FxMain"), "Animator", "FastDisplay", "1")
    assert out == (
        b"[CtEdit]\r\nlastdatabase =FxMain\r\n\r\n[Page]\r\nScanTime=250\r\n\r\n"
        b"[Animator]\r\nFastDisplay=1\r\n"
    )
    assert fo.ini_get(out, "ctedit", "LASTDATABASE") == "FxMain"
    assert fo.safe_name("fx_blå_sø") == "fx_bl%E5_s%F8"
    assert fo.safe_name("!fx_popup@SUPER") == "!fx_popup@SUPER"


class _FakeBuilder:
    """A stand in for the Graphics Builder's COM: PageSave writes what a real save writes
    (the page, a .ctF, a pages.DBF row, an index, a super genie page holding a path)."""

    def __init__(self, user):
        self.user = user
        self.page = None
        self.selected = []

    def ProjectSelect(self, name):
        self.project = name

    def PageOpen(self, project, page):
        self.page = (project, page)

    def PageOpenEx(self, project, _library, element, _ptype):
        self.page = (project, element)

    def PageName(self):
        return "%s - %s" % self.page if self.page else ""

    def PageClose(self):
        self.page = None

    def PageSelectObject(self, an):
        if an == 0:
            raise RuntimeError("no object 0")
        self.selected = [an]

    def PageSelectObjectAdd(self, an):
        self.selected.append(an)

    def AttributeAN(self):
        return self.selected[-1]

    def AttributeClass(self):
        return "Fx"

    def PositionRotate(self):
        self.turned = True

    def PageSave(self):
        from ctgedit.format import dbf

        project, page = self.page
        folder = os.path.join(self.user, project)
        with open(os.path.join(folder, page + ".ctg"), "ab") as fh:
            fh.write(b"saved by the fake")
        with open(os.path.join(folder, page + ".ctF"), "wb") as fh:
            fh.write(b"a fast display")
        with dbf.Dbf(os.path.join(folder, "pages.DBF")) as t:
            t.append([t.encode({"NAME": page, "TITLE": "Fx saved"})])
        with open(os.path.join(folder, "pages.NDX"), "ab") as fh:
            fh.write(b"\0" * 512)
        with open(os.path.join(self.user, "FxLib", "!fx_new.ctg"), "wb") as fh:
            fh.write(b"refers to " + self.user.encode())


def test_the_capture_collects_resets_and_writes_the_manifest(fo, tmp_path):
    """The capture's bookkeeping with a stand in for CtDraw: the outputs, the rows, the
    withheld file, the manifest, the folders put back after each unit."""
    import json

    root = _install(tmp_path)
    ch = _change(fo, tmp_path, root)
    ch.apply()
    user = str(root / "User")
    out = tmp_path / "oracle"
    prepared = {
        "inputs": {},
        "names": {
            "FxMain/FxArrange": {
                "names": {"FxPointGenie": {"an": 104, "root": True}, "FxLeafBefore": {"an": 2, "root": False}}
            }
        },
    }
    try:
        o = fo.Oracle(ch, str(out), prepared, str(tmp_path / "prep"), shots="none")
        o.pause = 0
        # (the turns step starts in a fresh Graphics Builder: a stand in for it too)
        o.builder_factory, o.end_builder = (lambda: _Builder(user)), (lambda: None)
        o.take_baseline()
        o.com = fo.LoggedCom(_FakeBuilder(user), o.log)
        shapes = fo.select_units("pages:FxMain/FxShapes")
        turn = fo.select_units("turns:T01*")
        bad = fo.Unit(
            "turns",
            "Fx_not_a_root",
            "a member",
            [],
            fo._run_turn,
            sources=["FxMain/FxArrange.ctg"],
            data={"project": "FxMain", "page": "FxArrange", "names": ["FxLeafBefore"], "ops": ["R"]},
        )
        o.run_units(shapes + turn + [bad])
        o.close()
        assert o.failures == ["turns:Fx_not_a_root"]
        page = out / "pages" / "FxMain" / "FxShapes"
        fixture = os.path.join(fo.FIXTURES, "User", "FxMain", "FxShapes.ctg")
        assert (page / "page.ctg").read_bytes() == open(fixture, "rb").read() + b"saved by the fake"
        assert (page / "page.ctF").read_bytes() == b"a fast display"
        rows = json.loads((page / "rows.json").read_text(encoding="utf-8"))
        added = rows["tables"]["FxMain/pages.DBF"]["added"]
        assert added[-1] == {"NAME": "FxShapes", "TITLE": "Fx saved", "_flag": " "}
        assert o._scrub({"at": user.upper() + r"\FxMain"}) == {"at": r"<FX>\FxMain"}
        assert "FxMain/pages.NDX" in rows["indexes"]
        assert not (page / "files" / "FxMain" / "pages.DBF").exists()  # (rows only)
        assert not (page / "files" / "FxLib" / "!fx_new.ctg").exists()  # (a path in it)
        man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert set(man["out_of_scope"]) == set(fo.OUT_OF_SCOPE)
        files = {e["file"]: e for e in man["files"]}
        e = files["pages/FxMain/FxShapes/page.ctg"]
        assert e["source"] == "FxMain/FxShapes.ctg"
        assert e["source_sha1"] == fo.file_sha1(fixture)
        assert e["sha1"] == fo.sha1((page / "page.ctg").read_bytes())
        assert "tests/test_foundation.py" in e["tests"] and e["captured"]
        assert files["pages/FxMain/FxShapes/files/FxLib/!fx_new.ctg"]["withheld"]
        seq = json.loads((out / "turns" / "T01_point_turn" / "sequence.json").read_text(encoding="utf-8"))
        assert seq["an"] == {"selection": [104]}
        assert man["units"]["turns:Fx_not_a_root"]["status"] == "failed"
        assert "not a root" in man["units"]["turns:Fx_not_a_root"]["note"]
        assert man["units"]["pages:FxMain/FxShapes"]["status"] == "captured"
        assert user.lower() not in (out / "manifest.json").read_text(encoding="utf-8").lower()
        # the folders are as the install change left them
        assert (root / "User" / "FxMain" / "FxShapes.ctg").read_bytes() == open(fixture, "rb").read()
        assert not (root / "User" / "FxMain" / "FxShapes.ctF").exists()
        assert not (root / "User" / "FxLib" / "!fx_new.ctg").exists()
        # a rerun of a unit replaces its own entries only
        o2 = fo.Oracle(ch, str(out), prepared, str(tmp_path / "prep"), shots="none")
        o2.pause = 0
        o2.take_baseline()
        o2.com = fo.LoggedCom(_FakeBuilder(user), o2.log)
        o2.run_units(turn)
        o2.close()
        man2 = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert [e["file"] for e in man2["files"]] == [e["file"] for e in man["files"]]
    finally:
        assert ch.restore()


def _master_add(path, name):
    from ctgedit.format import dbf

    with dbf.Dbf(str(path)) as t:
        t.append([t.encode({"NAME": name, "PATH": "D:\\FxElsewhere\\%s\\" % name})])


@pytest.mark.skipif(os.name != "nt", reason="a file held open blocks its delete on Windows only")
def test_a_held_file_keeps_the_marker_and_the_next_restore_finishes(fo, tmp_path, monkeypatch):
    """A file in a copy is held open during the restore: the folder delete fails part way,
    but the marker stays (it goes last), so the next restore deletes the rest. The files
    were put back by the first restore: the second one leaves them as they are now."""
    from ctgedit.format.projects import read_dbf_rows

    monkeypatch.setattr(fo, "RMTREE_TRIES", 1)
    monkeypatch.setattr(fo, "RMTREE_PAUSE", 0.0)
    root = _install(tmp_path)
    before = _tree(root)
    lines = []
    ch = _change(fo, tmp_path, root, log=lines.append)
    ch.apply()
    main = root / "User" / "FxMain"
    held = open(main / "pages.DBF", "rb")  # (no FILE_SHARE_DELETE: the delete fails)
    try:
        assert not ch.restore()
        assert (main / fo.MARKER).read_bytes() == ch.state["run_id"].encode()
        assert (main / "pages.DBF").exists()
        assert ch.state["files_restored"] and ch.state["phase"] != "restored"
        assert not (root / "User" / "FxLib").exists()
    finally:
        held.close()
    # the user registers a project of their own before the next restore
    _master_add(root / "User" / "MASTER.DBF", "FxUserLater")
    again = fo.change_from_state(ch.run_dir, log=lines.append, processes=dict)
    assert again.restore()
    assert not main.exists()
    names = [r["NAME"] for r in read_dbf_rows(str(root / "User" / "MASTER.DBF"))]
    assert names == ["FxUserProject", "FxUserLater"]  # (not put back again)
    after = _tree(root)
    assert {k for k in after if after[k] != before.get(k)} == {os.path.join("User", "MASTER.DBF")}
    assert any("only the folders are checked" in t for t in lines)


@pytest.mark.skipif(os.name != "nt", reason="a file held open blocks its delete on Windows only")
def test_the_marker_is_deleted_last(fo, tmp_path, monkeypatch):
    monkeypatch.setattr(fo, "RMTREE_TRIES", 1)
    monkeypatch.setattr(fo, "RMTREE_PAUSE", 0.0)
    folder = tmp_path / "FxMain"
    (folder / "sub").mkdir(parents=True)
    for name in ("!FxSystem.ctg", fo.MARKER, "pages.DBF", "zz.ctg"):
        (folder / name).write_bytes(b"fx")
    (folder / "sub" / "fx.ctg").write_bytes(b"fx")
    with open(folder / "pages.DBF", "rb"):
        assert not fo._rmtree(str(folder), lambda _t: None)
        assert sorted(os.listdir(folder)) == sorted([fo.MARKER, "pages.DBF"])
    assert fo._rmtree(str(folder), lambda _t: None)
    assert not folder.exists()


def test_a_file_changed_after_the_run_is_not_overwritten(fo, tmp_path, monkeypatch):
    """A later restore (the run died; the user went on working) keeps a MASTER.DBF with a
    project row and a citect.ini with a line that are not the run's, unless --force."""
    from ctgedit.format.projects import read_dbf_rows

    root = _install(tmp_path)
    before = _tree(root)
    lines = []
    ch = _change(fo, tmp_path, root, log=lines.append)
    ch.apply()
    ini = root / "Config" / "citect.ini"
    _master_add(root / "User" / "MASTER.DBF", "FxUserLater")
    ini.write_bytes(fo.ini_set(ini.read_bytes(), "Page", "ScanTime", "100"))
    assert not ch.restore()
    names = [r["NAME"] for r in read_dbf_rows(str(root / "User" / "MASTER.DBF"))]
    assert "FxUserLater" in names
    assert fo.ini_get(ini.read_bytes(), "Page", "ScanTime") == "100"
    assert not (root / "User" / "FxMain").exists()  # (the folders went)
    refused = [t for t in lines if t.startswith("NOT restored, it changed after the run")]
    assert len(refused) == 2
    assert "fxuserlater" in refused[0] and "[page] scantime" in refused[1]
    assert "files_restored" not in ch.state
    monkeypatch.setattr(fo, "running_programs", dict)
    assert fo.main(["restore", "--backup", ch.run_dir]) == 1  # (still refused)
    assert fo.main(["restore", "--backup", ch.run_dir, "--force"]) == 0
    assert _tree(root) == before


def test_what_ctdraw_writes_into_the_ini_does_not_stop_a_restore(fo, tmp_path):
    """The lines CtDraw and Studio write while they run (window places, recent files, the
    run's own keys) are not a change of the user's: a later restore puts the ini back."""
    root = _install(tmp_path)
    before = _tree(root)
    ch = _change(fo, tmp_path, root)
    ch.apply()
    ini = root / "Config" / "citect.ini"
    raw = ini.read_bytes() + (
        b"\r\n[CTDRAW.windowplacement]\r\nmain=0,1\r\n\r\n[Recent File List]\r\nFile1=FxMain\\FxShapes\r\n"
    )
    ini.write_bytes(fo.ini_set(raw, "CtEdit", "LastDatabase", "FxSim"))
    again = fo.change_from_state(ch.run_dir, processes=dict, log=lambda _t: None)
    assert again.restore()
    assert _tree(root) == before


def test_write_leaves_no_tmp_file_when_it_fails(fo, tmp_path):
    target = tmp_path / "FxTarget"
    target.mkdir()  # (a folder: the replace fails)
    with pytest.raises(OSError):
        fo._write(str(target), b"fx")
    assert sorted(os.listdir(tmp_path)) == ["FxTarget"]


def _no_com(fo, monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a trial must start no COM, Studio or prepare step")

    for name in ("Oracle", "stop_studio", "run_prepare", "running_programs"):
        monkeypatch.setattr(fo, name, refuse)


def test_a_trial_on_a_fake_install_starts_no_com(fo, tmp_path, monkeypatch, capsys):
    root = _install(tmp_path)
    before = _tree(root)
    monkeypatch.setenv("CTG_SCADA_ROOT", "none")  # (cmd_run sets it for the run)
    monkeypatch.setenv("CTG_NO_STUDIO", "1")
    _no_com(fo, monkeypatch)
    runs = tmp_path / "runs"
    assert (
        fo.main(["run", "--install-root", str(root), "--backup-root", str(runs), "--steps", "compile"]) == 0
    )
    out = capsys.readouterr().out
    assert "the COM capture is skipped" in out and "Restore verified" in out
    assert _tree(root) == before
    assert fo.open_runs(str(runs)) == []


def test_a_restore_that_stops_says_the_install_is_still_changed(fo, tmp_path, monkeypatch, capsys):
    root = _install(tmp_path)
    before = _tree(root)
    monkeypatch.setenv("CTG_SCADA_ROOT", "none")
    _no_com(fo, monkeypatch)
    real = fo.InstallChange.restore

    def still_running(self, stop=True, force=False):
        raise fo.Refused("These still run: CtDraw32.exe.")

    monkeypatch.setattr(fo.InstallChange, "restore", still_running)
    runs = tmp_path / "runs"
    assert fo.main(["run", "--install-root", str(root), "--backup-root", str(runs)]) == 2
    out = capsys.readouterr().out
    run = fo.open_runs(str(runs))[0]
    assert "THE INSTALL IS STILL CHANGED" in out and "These still run: CtDraw32.exe" in out
    assert 'restore --backup "%s" --stop-studio' % run in out
    monkeypatch.setattr(fo.InstallChange, "restore", real)
    monkeypatch.setattr(fo, "running_programs", dict)
    assert fo.main(["restore", "--backup", run]) == 0
    assert _tree(root) == before


def test_the_watchdog_ends_ctdraw_once_per_late_call(fo):
    clock = fo.ComClock()
    kills, lines = [], []
    w = fo.DialogWatch(lines.append, clock, deadline=10.0, kill=lambda log: kills.append(1))
    assert not w.check_deadline(1000.0)  # (no call runs)
    clock.call = ("PageOpen", 100.0)
    assert not w.check_deadline(105.0)
    assert w.check_deadline(111.0) and not w.check_deadline(150.0)
    clock.call = ("PageSave", 200.0)
    assert w.check_deadline(211.0)
    assert kills == [1, 1] and "PageSave" in lines[-1]
    assert not fo.DialogWatch(lines.append, clock, deadline=0).check_deadline(10**6)

    class Fx:
        def Probe(self):
            assert clock.call[0] == "Probe"
            return 1

        def Fails(self):
            raise RuntimeError("fx")

    clock.call = None
    com = fo.LoggedCom(Fx(), lambda _t: None, clock)
    assert com.Probe() == 1 and clock.call is None
    with pytest.raises(RuntimeError):
        com.Fails()
    assert clock.call is None


#: what prepare maps on the cut_link page (the template root, the links the cut keeps)
CUT_TEMPLATE = {
    "an": 11,
    "handle": 10,
    "index": 0,
    "reference": "fx_style.normal@XGA.<ctt",
    "keep": ["fxgenie.fx_pump.<ctm"],
}


@pytest.mark.parametrize("cuts", [True, False])
def test_the_cut_link_unit_selects_the_template_and_checks_the_cut(fo, tmp_path, monkeypatch, cuts):
    """Break Lock Mode on (template objects are selectable only then), the template's root
    selected, Cut Link posted, the page saved, Break Lock Mode off again. A save that still
    names the template (Cut Link with nothing selected changes nothing) fails the unit, so
    it is never recorded as captured."""
    import json
    import re

    root = _install(tmp_path)
    ch = _change(fo, tmp_path, root)
    ch.apply()
    out = tmp_path / "oracle"
    user = str(root / "User")
    fake = _FakeBuilder(user)
    fake.cut = False
    real_save = fake.PageSave

    def save():
        real_save()
        if fake.cut:  # (the template's reference leaves the page)
            path = os.path.join(user, "FxMain", fo.CUT_LINK_PAGE + ".ctg")
            with open(path, "rb") as fh:
                data = re.sub(rb"(?i)fx_style\.normal@xga\.<ctt", b"", fh.read())
            with open(path, "wb") as fh:
                fh.write(data)

    fake.PageSave = save
    lock = {"on": False}
    posted = []

    def menu(command_id):
        posted.append(command_id)
        if command_id == fo.MENU_BREAK_LOCK:
            lock["on"] = not lock["on"]
        elif command_id == fo.MENU_CUT_LINK:
            fake.cut = cuts and lock["on"] and fake.selected == [CUT_TEMPLATE["an"]]

    try:
        o = fo.Oracle(
            ch,
            str(out),
            {"inputs": {}, "names": {}, "templates": {"FxMain/" + fo.CUT_LINK_PAGE: CUT_TEMPLATE}},
            str(tmp_path / "prep"),
            shots="none",
        )
        o.pause = 0
        monkeypatch.setattr(o, "menu_command", menu)
        monkeypatch.setattr(
            o, "menu_checked", lambda command_id: lock["on"] if command_id == fo.MENU_BREAK_LOCK else None
        )
        # (the same fake each time: the reopen of the cut page asks for a fresh builder)
        o.builder_factory, o.end_builder = (lambda: _Builder.of(fake)), (lambda: None)
        o.take_baseline()
        o._attach()
        o.run_units(fo.select_units("newpage:cut_link"))
        o.close()
        assert posted == [fo.MENU_BREAK_LOCK, fo.MENU_CUT_LINK, fo.MENU_BREAK_LOCK]
        assert not lock["on"]
        unit = out / "newpage" / "cut_link"
        acts = json.loads((unit / "actions.json").read_text(encoding="utf-8"))
        assert acts["menu_command"] == fo.MENU_CUT_LINK and acts["page"] == fo.CUT_LINK_PAGE
        assert acts["selected"] == "PageSelectObject(11)" and acts["break_lock_posted"]
        assert acts["links_lost"] == [] and not acts["unchanged"]
        assert acts["template_reference_left"] is not cuts
        assert (unit / "page.ctg").read_bytes().endswith(b"saved by the fake")
        man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        status = man["units"]["newpage:cut_link"]
        if cuts:
            assert o.failures == [] and status["status"] == "captured"
            # CtDraw's next PageOpen + PageSave of the cut page, in a fresh builder
            assert o.restarts == 1
            assert (unit / "reopened.ctg").read_bytes().endswith(b"saved by the fake")
        else:
            assert o.failures == ["newpage:cut_link"] and status["status"] == "failed"
            assert "still names fx_style.normal@XGA.<ctt" in status["note"]
    finally:
        assert ch.restore()


def test_the_elements_step_runs_last_of_the_ctdraw_steps(fo):
    """Its saves change the elements CtDraw holds in memory, so every other step that
    drives the Graphics Builder runs before it; the compiler does not drive it. The
    libraries that nest genies, and the nested placement, ask for a fresh builder."""
    steps = list(fo.STEPS)
    assert steps[-2:] == ["elements", "compile"]
    assert [s for s in steps if not fo.STEPS[s].builder] == ["compile"]
    nested = {u.name for u in fo.select_units("elements") if u.data.get("fresh")}
    assert nested == {"FxStyle/fx_style.ctt", "FxLib/fxnest.ctm", "FxLib/fxparts.ctm"}
    assert fo.select_units("place:nested_genie")[0].data["fresh"]


def test_the_helpers_of_a_fresh_builder_and_the_canvas(fo):
    # the elements a page or a block names, of those a builder has opened
    data = b"\x10Xfxsym.fx_box.<ctl#0a0b0c0d\x00FXNEST.n1.<ctm"
    opened = {("fxsym", "fx_box"), ("fxnest", "n1"), ("fxnest", "n0"), ("sym", "fx_box")}
    assert fo.linked_elements(data, opened) == ["fxnest.n1", "fxsym.fx_box", "sym.fx_box"]
    assert fo.linked_elements(data, set()) == []
    # the client area of a maximised MDI child in the grab of its whole window rect
    assert fo.client_box((-8, 55, 1928, 970), (0, 86), (1920, 876)) == (8, 31, 1928, 907)
    tree = [
        {
            "id": 0,
            "has_submenu": True,
            "items": [
                {"id": 133, "has_submenu": False, "checked": False},
                {"id": 0, "has_submenu": True, "items": [{"id": 148, "has_submenu": False, "checked": True}]},
            ],
        }
    ]
    assert fo.find_menu_item(tree, 148)["checked"]
    assert fo.find_menu_item(tree, 999) is None


class _Builder:
    """A stand in for GraphicsBuilder: its COM and its close."""

    def __init__(self, user):
        self.com = _FakeBuilder(user)

    @classmethod
    def of(cls, com):
        """A stand in around the fake COM ``com`` (kept across a restart)."""
        b = cls.__new__(cls)
        b.com = com
        return b

    def close(self):
        pass


#: what prepare maps on FxArrange for the turn units these tests run
ARRANGE = {
    "inputs": {},
    "names": {"FxMain/FxArrange": {"names": {"FxPointGenie": {"an": 104, "root": True}}}},
}


def test_each_step_and_each_nested_element_gets_a_fresh_builder(fo, tmp_path):
    """A restart before each step (pages, turns, elements) and before each element of
    fxnest that nests one this builder has opened (n1 nests n0 ...). What the restart
    writes (Studio's start) goes into the baseline; what the unit wrote before it stays the
    unit's."""
    import json

    root = _install(tmp_path)
    ch = _change(fo, tmp_path, root)
    ch.apply()
    user = str(root / "User")
    out = tmp_path / "oracle"
    attached, ended = [], []

    def factory():
        attached.append(1)
        return _Builder(user)

    def end():
        ended.append(1)
        (root / "User" / "FxMain" / "fx_studio.ini").write_bytes(b"restart %d" % len(ended))

    try:
        o = fo.Oracle(ch, str(out), ARRANGE, str(tmp_path / "prep"), shots="none")
        o.pause = 0
        o.builder_factory, o.end_builder = factory, end
        o.take_baseline()
        o._attach()
        units = fo.select_units("elements:FxLib/fxnest.ctm,turns:T01*,pages:FxMain/FxShapes")
        assert [u.key for u in units] == [
            "pages:FxMain/FxShapes",
            "turns:T01_point_turn",
            "elements:FxLib/fxnest.ctm",
        ]
        o.run_units(units)
        o.close()
        assert o.failures == []
        assert (len(attached), len(ended), o.restarts) == (7, 6, 6)
        unit = out / "elements" / "FxLib" / "fxnest.ctm"
        got = json.loads((unit / "elements.json").read_text(encoding="utf-8"))
        assert got["saved"] == ["n0", "n1", "n2", "n3", "n4"]
        assert got["fresh_builder_before"] == ["n1", "n2", "n3", "n4"]
        assert (unit / "files" / "FxLib" / "n0.ctg").is_file()  # (before a restart)
        assert not list(out.rglob("fx_studio.ini"))  # (Studio's, not a unit's)
        assert (root / "User" / "FxMain" / "fx_studio.ini").read_bytes() == b"restart 6"
        # what needs one: a step's first unit, a unit that asks, a source that names an
        # element this builder has opened; the compiler never
        o.used = True
        o.opened = {("fxsym", "fx_box")}
        shapes, symbols = fo.select_units("pages:FxMain/FxShapes,pages:FxMain/FxSymbols")
        assert o.fresh_reason(shapes, shapes) is None
        assert "fxsym.fx_box" in o.fresh_reason(symbols, shapes)
        assert "asks for one" in o.fresh_reason(
            fo.select_units("place:nested_genie")[0], fo.select_units("place:linked_symbol")[0]
        )
        assert o.fresh_reason(fo.select_units("compile:FxMain")[0], units[-1]) is None
        assert "turns step starts" in o.fresh_reason(units[1], units[0])
    finally:
        assert ch.restore()


def test_a_builder_that_does_not_end_stops_the_run(fo, tmp_path):
    import json

    root = _install(tmp_path)
    ch = _change(fo, tmp_path, root)
    ch.apply()
    user = str(root / "User")
    out = tmp_path / "oracle"
    try:
        o = fo.Oracle(ch, str(out), ARRANGE, str(tmp_path / "prep"), shots="none")
        o.pause = 0
        o.builder_factory, o.end_builder = (lambda: _Builder(user)), (lambda: None)
        o.take_baseline()
        o._attach()
        ch.processes = lambda: {"CtDraw32.exe": [4321]}  # (it never ends)
        with pytest.raises(fo.BuilderLost, match="4321"):
            o.run_units(fo.select_units("pages:FxMain/FxShapes,turns:T01*,turns:T02*"))
        o.close()
        assert o.failures == ["turns:T01_point_turn"]
        man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert man["units"]["turns:T01_point_turn"]["status"] == "failed"
        assert "turns:T02_point_two_turns" not in man["units"]
    finally:
        ch.processes = dict
        assert ch.restore()


def test_a_fresh_builder_for_an_opened_element_and_after_the_builder_ended(fo, tmp_path):
    """The append unit opens fxmany.fx_last_big, which the fxmany.ctl unit opened and saved:
    it gets a fresh builder. A builder whose CtDraw32 has ended (the watchdog) is replaced
    before the next unit, though it has opened nothing since."""
    root = _install(tmp_path)
    ch = _change(fo, tmp_path, root)
    ch.apply()
    user = str(root / "User")
    out = tmp_path / "oracle"
    attached = []

    def factory():
        attached.append(1)
        return _Builder(user)

    try:
        o = fo.Oracle(ch, str(out), ARRANGE, str(tmp_path / "prep"), shots="none")
        o.pause = 0
        o.builder_factory, o.end_builder = factory, (lambda: None)
        o.take_baseline()
        append = fo.select_units("elements:append")[0]
        many = fo.select_units("elements:FxLib/fxmany.ctl")[0]
        o.used, o.opened = True, {("fxmany", "fx_last_big")}
        assert "fxmany.fx_last_big" in o.fresh_reason(append, many)
        o.opened = {("fxmany", "fx_other")}
        assert o.fresh_reason(append, many) is None
        # the builder the run attached to ends: the next unit gets a new one
        ch.processes = lambda: {"CtDraw32.exe": [4321]}
        o._attach()
        assert o.pids == {4321} and not o.builder_gone()
        ch.processes = dict  # (the watchdog ended it)
        assert o.builder_gone() and not o.used
        units = fo.select_units("turns:T01*,turns:T02*")
        assert o.fresh_reason(units[1], units[0]) == "the Graphics Builder has ended"
        o.run_units(units[1:])
        o.close()
        assert o.failures == [] and len(attached) == 2 and o.restarts == 1
    finally:
        ch.processes = dict
        assert ch.restore()


def test_a_listed_shot_that_fails_fails_the_unit(fo, tmp_path, monkeypatch):
    """A render page whose canvas.png could not be made (no canvas, a client area outside
    the grab) is not recorded as captured: its golden tests would skip for good."""
    import json

    root = _install(tmp_path)
    ch = _change(fo, tmp_path, root)
    ch.apply()
    out = tmp_path / "oracle"
    try:
        o = fo.Oracle(ch, str(out), ARRANGE, str(tmp_path / "prep"), shots="listed")
        o.pause = 0
        monkeypatch.setattr(o, "shot", lambda: None)
        o.take_baseline()
        o.com = fo.LoggedCom(_FakeBuilder(str(root / "User")), o.log)
        assert fo.select_units("pages:FxMain/FxShapes")[0].data["shot"]
        o.run_units(fo.select_units("pages:FxMain/FxShapes"))
        o.close()
        assert o.failures == ["pages:FxMain/FxShapes"]
        man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert "no canvas.png" in man["units"]["pages:FxMain/FxShapes"]["note"]
        assert (out / "pages" / "FxMain" / "FxShapes" / "page.ctg").is_file()
    finally:
        assert ch.restore()


def test_select_all_that_selects_nothing_fails_the_cut_link(fo, tmp_path):
    """The template's PageSelectObject fails, then Select All leaves AN 0: nothing to cut."""
    root = _install(tmp_path)
    ch = _change(fo, tmp_path, root)
    ch.apply()
    try:
        o = fo.Oracle(ch, str(tmp_path / "oracle"), ARRANGE, str(tmp_path / "prep"), shots="none")
        o.pause = 0
        fake = _FakeBuilder(str(root / "User"))
        fake.PageSelectObject = lambda an: (_ for _ in ()).throw(RuntimeError("no"))
        fake.AttributeAN = lambda: 0
        o.com = fo.LoggedCom(fake, o.log)
        o.menu_command = lambda command_id: None
        with pytest.raises(fo.UnitFailed, match="AN 0"):
            o.select_template(11)
        fake.AttributeAN = lambda: 12
        assert o.select_template(11) == "Select All (menu 133), AN 12 first"
        o.close()
    finally:
        assert ch.restore()
