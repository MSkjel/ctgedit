"""Replace Genie."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.doc import genieswap as GS  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import writer  # noqa: E402
from ctgedit.format.symbols import reference_from_object  # noqa: E402


# ------------------------------------------------------------------ the pure helpers
def test_suggested_mapping_matches_names_without_case():
    m = GS.suggested_mapping(["TAG", "Label", "AN", "!*PropSheet.Focus"], ["Tag", "Colour"])
    assert dict(m) == {"Tag": "%TAG%"}


def test_map_value_substitutes_old_bindings_and_keeps_unknown_tokens():
    old = {"Tag": "PUMP_1", "Label": "Feed"}
    assert GS.map_value("%tag%_PV", old) == "PUMP_1_PV"
    assert GS.map_value("%Label% (%Tag%)", old) == "Feed (PUMP_1)"
    assert GS.map_value("%Other%", old) == "%Other%"
    assert GS.map_value("", old) == ""
    assert GS.mapping_tokens({"A": "%Tag%_%LABEL%", "B": "%tag%"}) == ["Tag", "LABEL"]


def test_tokens_are_read_as_the_composition_reads_them():
    """Parameter tokens are read the way the composition reads them."""
    old = {"up/down1left/right2": "3", "1up2right3down4left": "%Dir=1%", "buttonText ": "Start", "Empty": ""}
    assert GS.map_value("%up/down1left/right2%", old) == "3"
    assert GS.map_value("%1UP2RIGHT3DOWN4LEFT%", old) == "%Dir=1%"
    assert GS.map_value("%buttonText %!", old) == "Start!"
    assert GS.map_value("%Empty=7%", old) == "7"
    assert GS.map_value("%up/down1left/right2=9%", old) == "3"
    assert GS.map_value("100%% %Other/x%", old) == "100%% %Other/x%"
    assert GS.map_value("50% left", old) == "50% left"
    assert GS.mapping_tokens({"A": "%up/down1left/right2%", "B": "%Up=1%_%x y%"}) == [
        "up/down1left/right2",
        "Up",
        "x y",
    ]


# ------------------------------------------------------------------ on a page
def _open(fx):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(fx.page("FxBig", "FxMain"))


def _link(doc, i):
    ref = reference_from_object(doc.parsed.page.objects[i])
    return ref.library, ref.element


def _box(doc, i):
    return tuple(G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1))


def _member_values(doc, root):
    """Every value of the members' own level (b1 of their entry 0)."""
    out = []
    for j in doc.subtree(root):
        if j == root:
            continue
        r = GP.decode(doc.parsed.page.objects[j])
        for k, v in r.entries[0][1].pairs if r.entries else ():
            out.append(v)
    return out


def _link_names(doc):
    from ctgedit.format import links

    pb = doc.parsed.page
    t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
    return [n.split("#")[0].lower() for _g, n in (t[1] if t else [])]


def test_same_named_parameter_carries_over(fx_install):
    doc = _open(fx_install)
    i = doc.place_library_element(
        "fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "PUMP_7", "Label": "Feed"}
    )
    handle = G.get_handle(doc.parsed.page.objects[i])
    before = [writer.object_payload(o) for o in doc.parsed.page.objects]
    assert doc.replaceable_genies([i]) == [(i, ("fxgenie", "fx_pump"))]
    assert doc.replace_genies([i], "fxgenie", "fx_lamp") == 1
    assert _link(doc, i) == ("fxgenie", "fx_lamp")
    assert dict(doc.genie_instance_bindings(i)) == {"Tag": "PUMP_7"}  # Label dropped
    assert any("PUMP_7" in v for v in _member_values(doc, i))
    assert G.get_handle(doc.parsed.page.objects[i]) == handle
    assert "fxgenie.fx_lamp.<ctm" in _link_names(doc)
    # one undo step back to the page as it was
    doc.undo()
    assert [writer.object_payload(o) for o in doc.parsed.page.objects] == before


def test_mapping_binds_a_differently_named_parameter(fx_install):
    doc = _open(fx_install)
    i = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "P1", "Label": "L"})
    doc.replace_genies([i], "fxgenie", "fx_valve", {"TagPrefix": "%Tag%_V"})
    assert _link(doc, i) == ("fxgenie", "fx_valve")
    assert dict(doc.genie_instance_bindings(i)) == {"TagPrefix": "P1_V"}


def test_a_parameter_the_mapping_leaves_out_takes_its_default(fx_install):
    doc = _open(fx_install)
    i = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "P1", "Label": "L"})
    doc.replace_genies([i], "fxparts", "fx_default")
    assert dict(doc.genie_instance_bindings(i)) == {"Tag": "P1", "Title": "Pump"} or dict(
        doc.genie_instance_bindings(i)
    ) == {"Title": "Pump", "Tag": "P1"}


def _local(doc, i):
    """The instance root's Local (slot 1: mode and matrix): the point its hot spot sits on,
    and its scale / turn."""
    from ctgedit.format import placement

    mode, m6, _o, _n = placement.read_slots(doc.parsed.page.objects[i].body_raw)[0][0]
    return mode, tuple(round(v, 6) for v in m6)


def test_the_new_hot_spot_goes_where_the_old_one_was(fx_install):
    """The new hot spot goes where the old one was."""
    doc = _open(fx_install)
    i = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "P1"})
    local = _local(doc, i)
    old_box = _box(doc, i)
    doc.replace_genies([i], "fxgenie", "fx_lamp")
    assert _local(doc, i) == local
    assert _box(doc, i)[:2] != old_box[:2]
    # a placement with its hot spot on the same point: top-left = hot spot - e + N.lt
    el = GP.load_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_lamp")
    hx, hy = local[1][4], local[1][5]
    k = doc.place_library_element(
        "fxgenie",
        "fx_lamp",
        "ctm",
        (int(hx - el.e[0] + el.N[0]), int(hy - el.e[1] + el.N[1])),
        params={"Tag": "P1"},
    )
    assert _local(doc, k) == _local(doc, i)
    assert [_box(doc, x) for x in doc.subtree(i)] == [_box(doc, y) for y in doc.subtree(k)]


def test_the_top_left_can_be_kept_instead(fx_install):
    doc = _open(fx_install)
    i = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "P1"})
    box = _box(doc, i)
    doc.replace_genies([i], "fxgenie", "fx_lamp", keep="topleft")
    assert _box(doc, i)[:2] == box[:2]


def test_the_replaced_instance_is_what_a_placement_composes(fx_install):
    """Members, values and geometry as a fresh placement of the new genie at the same
    top-left with the mapped values (the handles aside)."""
    doc = _open(fx_install)
    i = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "P1", "Label": "L"})
    doc.replace_genies([i], "fxgenie", "fx_lamp", keep="topleft")
    k = doc.place_library_element("fxgenie", "fx_lamp", "ctm", (300, 200), params={"Tag": "P1"})
    objs = doc.parsed.page.objects
    a, b = doc.subtree(i), doc.subtree(k)
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert objs[x].type_code == objs[y].type_code
        assert _box(doc, x) == _box(doc, y)
        ra, rb = GP.decode(objs[x]), GP.decode(objs[y])
        strip = lambda r: [
            [
                [(kk, vv) for kk, vv in bag.pairs if kk != "AN"]  # noqa: E731
                for bag in e
            ]
            for e in r.entries or ()
        ]
        assert strip(ra) == strip(rb)


def test_every_instance_at_once_and_the_old_link_goes(fx_install):
    doc = _open(fx_install)
    pumps = [i for i, link in doc.genie_instances() if link == ("fxgenie", "fx_pump")]
    doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "A"})
    doc.place_library_element("fxgenie", "fx_pump", "ctm", (400, 200), params={"Tag": "B"})
    pumps = [i for i, link in doc.genie_instances() if link == ("fxgenie", "fx_pump")]
    assert len(pumps) >= 3
    assert doc.replace_genies(pumps, "fxgenie", "fx_lamp") == len(pumps)
    links = [link for _i, link in doc.genie_instances()]
    assert ("fxgenie", "fx_pump") not in links
    assert links.count(("fxgenie", "fx_lamp")) == len(pumps)
    tags = sorted(
        dict(doc.genie_instance_bindings(i)).get("Tag", "")
        for i, link in doc.genie_instances()
        if link == ("fxgenie", "fx_lamp")
    )
    assert "A" in tags and "B" in tags
    assert "fxgenie.fx_pump.<ctm" not in _link_names(doc)


def test_the_saved_page_reads_back_current(fx_install):
    """Saved and opened again, the page's genies are current (Update Pages leaves it)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as UP

    doc = _open(fx_install)
    i = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "P1", "Label": "L"})
    doc.replace_genies([i], "fxgenie", "fx_lamp")
    doc.save()
    again = EditableDocument.open(doc.path)
    assert ("fxgenie", "fx_lamp") in [link for _i, link in again.genie_instances()]
    assert not UP.page_needs_update(doc.path)


def test_refusals(fx_install):
    doc = _open(fx_install)
    i = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "A"})
    with pytest.raises(ValueError):
        doc.replace_genies([i], "fxgenie", "no_such_genie")
    with pytest.raises(ValueError):
        doc.replace_genies([i], "nolib", "fx_lamp")
    plain = next(j for j, o in enumerate(doc.parsed.page.objects) if j not in dict(doc.genie_instances()))
    with pytest.raises(ValueError):
        doc.replace_genies([plain], "fxgenie", "fx_lamp")
    assert _link(doc, i) == ("fxgenie", "fx_pump")


# ------------------------------------------------------------------ the dialog
def test_dialog_fills_the_mapping_and_refuses_the_same_genie(fx_install):
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = _open(fx_install)
    i = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "P1", "Label": "L"})
    dlg = ReplaceGenieDialog(doc, [i])
    assert dlg.old_genies() == [("fxgenie", "fx_pump")]
    assert dlg.rb_selected.isChecked() and dlg.targets() == [i]
    assert dlg.rb_hotspot.isChecked() and dlg.keep() == "origin"  # the cross, at first
    dlg.rb_topleft.setChecked(True)
    assert dlg.keep() == "topleft" and dlg.rb_selected.isChecked()  # (apart from Which)
    dlg.rb_hotspot.setChecked(True)
    assert not dlg.ok.isEnabled()  # no new genie yet
    dlg.new_field.set_value(("fxgenie", "fx_pump"), None, emit=True)
    assert not dlg.ok.isEnabled()  # the same genie
    dlg.new_field.set_value(("fxparts", "fx_default"), None, emit=True)
    assert dlg.ok.isEnabled()
    assert dlg.mapping() == {"Tag": "%Tag%"}  # Title: blank, its default
    assert "Label" in dlg.dropped.text()
    rows = {dlg.table.item(r, 0).text(): r for r in range(dlg.table.rowCount())}
    dlg._value_box(rows["Title"]).setCurrentText("%Label% pump")
    assert dlg.mapping() == {"Tag": "%Tag%", "Title": "%Label% pump"}
    assert "Label" not in dlg.dropped.text()
    dlg.rb_every.setChecked(True)
    assert i in dlg.targets() and len(dlg.targets()) >= 2
    assert doc.replace_genies(dlg.targets(), "fxparts", "fx_default", dlg.mapping()) >= 2
    dlg.deleteLater()


# ------------------------------------------------------------------ every page of a project
def _pump_pages(fx):
    """The FxMain pages whose link table names fxgenie.fx_pump."""
    from ctgedit.workers import genieswap_worker as GW

    folder = fx.project("FxMain")
    return sorted(
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith(".ctg") and GW.places(os.path.join(folder, f), {("fxgenie", "fx_pump")})
    )


def test_find_lists_the_pages_with_an_instance_to_replace(fx_install):
    from ctgedit.workers import genieswap_worker as GW

    res = GW.find({"projects": [fx_install.project("FxMain")], "old": [["FXGENIE", "fx_pump"]]})
    names = {p["name"] for p in res["pages"]}
    assert "FxBig" in names and "FxGenies" in names
    assert all(p["count"] >= 1 and p["project"] == "FxMain" for p in res["pages"])
    assert "FxInputs" not in names  # (no fx_pump)
    assert res["checked"] > len(res["pages"])


def test_apply_replaces_backs_up_and_saves_every_page(fx_install):
    from ctgedit.workers import genieswap_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import backups

    found = GW.find({"projects": [fx_install.project("FxMain")], "old": [["fxgenie", "fx_pump"]]})
    pages = [p["page"] for p in found["pages"]]
    total = sum(p["count"] for p in found["pages"])
    skip = next(p for p in pages if p.endswith("FxBig.ctg"))
    folder = backups.new_folder(GW.BACKUP_KIND)
    res = GW.apply(
        {
            "pages": pages,
            "old": [["fxgenie", "fx_pump"]],
            "new": ["fxgenie", "fx_lamp"],
            "mapping": {},
            "keep": "topleft",
            "skip": [skip],
            "builder_open": [],
            "backup": folder,
        }
    )
    assert res["skipped_open"] == ["FxBig"]
    assert not res["failed"], res["failed"]
    assert len(res["updated"]) == len(pages) - 1
    assert res["changed"] == total - next(p["count"] for p in found["pages"] if p["page"] == skip)
    for p in pages:
        links = [link for _i, link in EditableDocument.open(p).genie_instances()]
        if p == skip:
            assert ("fxgenie", "fx_pump") in links
        else:
            assert ("fxgenie", "fx_pump") not in links and ("fxgenie", "fx_lamp") in links
    # every saved page was backed up first, under its own kind, and Restore Backup puts it
    # back
    man = backups.read_manifest(folder)
    assert man["kind"] == GW.BACKUP_KIND and GW.BACKUP_KIND in backups.PAGE_KINDS
    assert {e["page"] for e in man["entries"]} == set(res["updated"])
    from ctgedit.io import updatepages as UP

    UP.restore_backup(folder)
    for p in pages:
        assert ("fxgenie", "fx_pump") in [link for _i, link in EditableDocument.open(p).genie_instances()]


def test_a_genie_whose_library_is_missing_is_found_and_replaced(fx_install):
    """FxMissing places fxgone.fx_value (the library fxgone is not there): matched by the
    name its link spells, replaced with the real fxgenie.fx_value."""
    from ctgedit.workers import genieswap_worker as GW
    from ctgedit.editing import EditableDocument

    page = fx_install.page("FxMissing", "FxMain")
    doc = EditableDocument.open(page)
    gone = [i for i, link in doc.replaceable_genies() if link == ("fxgone", "fx_value")]
    if not gone:
        pytest.skip("FxMissing holds no replaceable fxgone.fx_value instance")
    res = GW.find({"projects": [fx_install.project("FxMain")], "old": [["fxgone", "fx_value"]]})
    assert any(p["name"] == "FxMissing" for p in res["pages"])
    res = GW.apply(
        {
            "pages": [page],
            "old": [["fxgone", "fx_value"]],
            "new": ["fxgenie", "fx_value"],
            "mapping": {},
            "keep": "topleft",
            "skip": [],
            "builder_open": [],
            "backup": None,
        }
    )
    assert res["updated"] == ["FxMissing"], res
    links = [link for _i, link in EditableDocument.open(page).genie_instances()]
    assert ("fxgone", "fx_value") not in links and ("fxgenie", "fx_value") in links


def test_the_worker_main_answers_a_request(fx_install, tmp_path):
    import json
    from ctgedit.workers import genieswap_worker as GW

    req, ans = tmp_path / "r.json", tmp_path / "a.json"
    req.write_text(
        json.dumps(
            {
                "format": GW.FORMAT,
                "mode": "find",
                "projects": [fx_install.project("FxMain")],
                "old": [["fxgenie", "fx_pump"]],
            }
        ),
        encoding="utf-8",
    )
    assert GW.main(["--replace-genie", str(req), str(ans)]) == 0
    out = json.loads(ans.read_text(encoding="utf-8"))
    assert not out.get("error") and out["pages"]
    req.write_text(json.dumps({"format": "other"}), encoding="utf-8")
    GW.main([str(req), str(ans)])
    assert "format" in json.loads(ans.read_text(encoding="utf-8"))["error"]


class _Bar:
    def showMessage(self, *a):  # noqa: N802
        self.last = a[0]


class _Win:
    """What ProjectReplaceRun reads of a window."""

    _replace_genie_quiet = True

    def __init__(self, doc=None):
        from PySide6 import QtCore

        self._qobj = QtCore.QObject()
        self.doc, self._sel, self._docs = doc, [], ([{"doc": doc}] if doc else [])
        self._bar = _Bar()
        self.refreshed = 0

    def statusBar(self):  # noqa: N802
        return self._bar

    def _refresh(self):
        self.refreshed += 1


def _run_in_process(run):
    """The helper's two steps in this process (the worker's own functions)."""
    from ctgedit.workers import genieswap_worker as GW

    def _run(req, label, done):
        done(GW.find(req) if req["mode"] == "find" else GW.apply(req))
        return True

    run._run = _run


def test_project_run_changes_the_open_page_in_its_tab_and_saves_the_others(fx_install, monkeypatch):
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument
    from ctgedit.ui import main_window as MW
    from ctgedit.ui.dialog import replace_genie as RG

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = EditableDocument.open(fx_install.page("FxBig", "FxMain"))
    win = _Win(doc)
    monkeypatch.setattr(MW.MainWindow, "instances", [win])
    asked = []
    run = RG.ProjectReplaceRun(
        win._qobj,
        [fx_install.project("FxMain")],
        [("fxgenie", "fx_pump")],
        ("fxgenie", "fx_lamp"),
        {},
        "topleft",
        confirm=lambda t, d: asked.append((t, d)) or True,
    )
    run.win = win
    _run_in_process(run)
    assert run.start() and not run.busy()
    assert asked and "with fxgenie.fx_lamp" in asked[0][0]
    assert "FxBig (open in the editor)" in asked[0][1]
    assert run.changed_open == ["FxBig"] and doc.dirty and win.refreshed
    assert ("fxgenie", "fx_pump") not in [l for _i, l in doc.genie_instances()]
    assert run.answer["updated"] and "FxBig" not in run.answer["updated"]
    assert "Open in the editor, not saved yet: FxBig." in run.result_text
    doc.undo()  # the tab's change is one step
    assert ("fxgenie", "fx_pump") in [l for _i, l in doc.genie_instances()]


def test_project_run_cancelled_writes_nothing(fx_install, monkeypatch):
    from PySide6 import QtWidgets
    from ctgedit.ui import main_window as MW
    from ctgedit.ui.dialog import replace_genie as RG

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    pages = _pump_pages(fx_install)
    before = {p: open(p, "rb").read() for p in pages}
    win = _Win()
    monkeypatch.setattr(MW.MainWindow, "instances", [win])
    run = RG.ProjectReplaceRun(
        win._qobj,
        [fx_install.project("FxMain")],
        [("fxgenie", "fx_pump")],
        ("fxgenie", "fx_lamp"),
        {},
        "topleft",
        confirm=lambda t, d: False,
    )
    run.win = win
    _run_in_process(run)
    run.start()
    assert not run.busy() and not run.answer
    assert {p: open(p, "rb").read() for p in pages} == before


def test_the_project_choice_is_find_replaces(fx_install):
    """All pages of the project: Find / Replace's project drop-down (the page's project
    first chosen) and 'And the projects it includes' (find_dialog.project_dirs)."""
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.find_dialog import project_choices, project_dirs
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = _open(fx_install)
    dlg = ReplaceGenieDialog(doc)
    projects, here = project_choices(doc.path)
    assert here == "FxMain" and dlg.cb_project.currentText() == "FxMain"
    assert [dlg.cb_project.itemText(k) for k in range(dlg.cb_project.count())] == [n for n, _d in projects]
    assert not dlg.cb_project.isEnabled()  # only with the option chosen
    dlg.rb_project.setChecked(True)
    assert dlg.cb_project.isEnabled() and dlg.project_wide()
    assert dlg.project_dirs() == project_dirs("FxMain", False) == [fx_install.project("FxMain")]
    dlg.cb_includes.setChecked(True)
    dirs = [os.path.basename(d).lower() for d in dlg.project_dirs()]
    assert dirs == [os.path.basename(d).lower() for d in project_dirs("FxMain", True)]
    assert dirs[0] == "fxmain" and "include" not in dirs and "system" not in dirs
    other = next(n for n, _d in projects if n != "FxMain")
    dlg.cb_project.setCurrentText(other)
    assert dlg.project_dirs()[0] == fx_install.project(other)
    dlg.deleteLater()


# ------------------------------------------------------------------ inside library genies
def _nest(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _strip(r):
    return [[[(k, v) for k, v in bag.pairs if k != "AN"] for bag in e] for e in r.entries or ()]


def test_a_sub_genie_in_a_genie_definition_is_replaced_in_the_element_form(fx_install):
    """n1 places n0 with Label '%Name% L1' (its own parameter Name): replaced with
    fxgenie.fx_value, Description '%Label%' gives the template '%Name% L1', the instance
    in the element form, as a placement of fx_value in n1 with that value gives it."""
    from ctgedit.editing import ElementDocument

    d = ElementDocument.open_element(_nest(fx_install), "n1")
    roots = [i for i, link in d.replaceable_genies() if link == ("fxnest", "n0")]
    assert len(roots) == 1
    assert dict(d.genie_instance_bindings(roots[0])) == {"Label": "%Name% L1"}
    assert d.replace_genies(roots, "fxgenie", "fx_value", {"Description": "%Label%"}) == 1
    i = d.replaced_roots[0]
    assert _link(d, i) == ("fxgenie", "fx_value")
    assert dict(d.genie_instance_bindings(i)) == {"Description": "%Name% L1"}
    assert len(GP.decode(d.parsed.page.objects[i]).entries) == 3  # the element form
    k = d.place_library_element("fxgenie", "fx_value", "ctm", (400, 300), params={"Description": "%Name% L1"})
    objs = d.parsed.page.objects
    a, b = d.subtree(i), d.subtree(k)
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert objs[x].type_code == objs[y].type_code
        assert _strip(GP.decode(objs[x])) == _strip(GP.decode(objs[y]))
    d.undo()
    d.undo()
    assert [link for _i, link in d.replaceable_genies()] == [("fxnest", "n0")]


def test_the_replaced_definition_saves_and_reads_back(fx_install):
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_nest(fx_install), "n1")
    roots = [i for i, _l in d.replaceable_genies()]
    d.replace_genies(roots, "fxgenie", "fx_value", {"Description": "%Label%"})
    d.save()
    libraries_changed()
    again = ElementDocument.open_element(_nest(fx_install), "n1")
    assert [l for _i, l in again.replaceable_genies()] == [("fxgenie", "fx_value")]
    i = again.replaceable_genies()[0][0]
    assert dict(again.genie_instance_bindings(i)) == {"Description": "%Name% L1"}
    assert list(again.genie_parameters("fxnest", "n1")) == ["Name"]


def test_a_sub_genie_never_takes_a_genie_that_holds_its_host(fx_install):
    """In n1, n0 cannot become n2 (n2 holds n1: n1 would hold itself)."""
    from ctgedit.editing import ElementDocument

    d = ElementDocument.open_element(_nest(fx_install), "n1")
    roots = [i for i, _l in d.replaceable_genies()]
    with pytest.raises(ValueError):
        d.replace_genies(roots, "fxnest", "n2")


def _projects(fx):
    return [fx.project("FxMain"), fx.project("FxLib")]


def _copy_records(page_path, host):
    """The records of the page's first copy of genie ``host`` (nested copies on a page
    carry no link: they are told by their records)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page_path)
    for i, link in doc.genie_instances():
        if link == host:
            return [writer.object_payload(doc.parsed.page.objects[j]) for j in doc.subtree(i)]
    return []


def _page_links(path):
    from ctgedit.io.genieusage import _entry_key
    from ctgedit.format import pagesummary

    return {k[:2] for k in map(_entry_key, pagesummary.read_link_names(path)) if k}


def test_find_lists_the_library_genies_and_what_follows_them(fx_install):
    from ctgedit.workers import genieswap_worker as GW

    res = GW.find({"projects": _projects(fx_install), "old": [["fxnest", "n0"]]})
    assert [e["label"] for e in res["elements"]] == ["fxnest.n1"]
    assert res["elements"][0]["count"] == 1 and res["elements"][0]["kind"] == "genie"
    assert "Label" in res["params"]
    # n2 .. n4 hold n0 only through n1 (their link tables name what they place directly:
    # n1, n2, n3): they follow n1, as users of it
    assert {"fxnest.n2", "fxnest.n3", "fxnest.n4"} <= set(res["follow_elements"])
    assert not {"fxnest.n2", "fxnest.n3", "fxnest.n4"} & {e["label"] for e in res["elements"]}
    assert "FxMain\\FxMembers" in res["follow_pages"]  # places n2
    assert "FxMain\\FxGenies" in res["follow_pages"]  # places n4
    assert not res["failed"], res["failed"]


def test_apply_changes_the_definition_then_brings_its_users_up_to_date(fx_install):
    from ctgedit.workers import genieswap_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.infra import backups
    from ctgedit.io import updatepages as UP

    found = GW.find({"projects": _projects(fx_install), "old": [["fxnest", "n0"]]})
    page = fx_install.page("FxGenies", "FxMain")
    copy_before = _copy_records(page, ("fxnest", "n4"))
    folder = backups.new_folder(GW.BACKUP_KIND)
    res = GW.apply(
        {
            "pages": [p["page"] for p in found["pages"]],
            "elements": [{"library": e["library"], "element": e["element"]} for e in found["elements"]],
            "old": [["fxnest", "n0"]],
            "new": ["fxgenie", "fx_value"],
            "mapping": {"Description": "%Label%"},
            "skip": [],
            "builder_open": [],
            "backup": folder,
        }
    )
    assert res["elements_updated"] == ["fxnest.n1"], res
    assert not res["failed"] and not res["chain_failed"], res
    assert {"fxnest.n2", "fxnest.n3", "fxnest.n4"} <= set(res["chain_elements_saved"])
    assert "FxGenies" in res["chain_pages_updated"]
    # FxMembers (places n2) is not registered in FxMain: left, as Update Pages leaves it
    assert "FxMembers" in res["chain_unregistered"]
    libraries_changed()
    n1 = ElementDocument.open_element(_nest(fx_install), "n1")
    assert [l for _i, l in n1.replaceable_genies()] == [("fxgenie", "fx_value")]
    # the page that places n4 composed its copy again: n0's group + text are fx_value's
    # three records now (one more), and the page is current
    copy_after = _copy_records(page, ("fxnest", "n4"))
    assert len(copy_after) == len(copy_before) + 1, (len(copy_before), len(copy_after))
    assert not UP.page_needs_update(page)
    # the library and the pages are in the one backup; Restore Backup puts them back
    man = backups.read_manifest(folder)
    assert man["kind"] == GW.BACKUP_KIND
    assert any(e.get("original", "").lower().endswith("fxnest.ctm") for e in man["entries"])
    UP.restore_backup(folder)
    libraries_changed()
    n1 = ElementDocument.open_element(_nest(fx_install), "n1")
    assert [l for _i, l in n1.replaceable_genies()] == [("fxnest", "n0")]
    assert _copy_records(page, ("fxnest", "n4")) == copy_before
    assert not UP.page_needs_update(page)


def test_a_library_genie_open_in_an_editor_is_left_to_its_tab(fx_install):
    from ctgedit.workers import genieswap_worker as GW

    lib = _nest(fx_install)
    before = open(lib, "rb").read()
    res = GW.apply(
        {
            "pages": [],
            "elements": [{"library": lib, "element": "n1"}],
            "old": [["fxnest", "n0"]],
            "new": ["fxgenie", "fx_value"],
            "mapping": {},
            "skip_elements": [[lib, "N1"]],
            "builder_open": [],
            "backup": None,
        }
    )
    assert res["elements_open"] == ["fxnest.n1"] and not res["elements_updated"]
    assert open(lib, "rb").read() == before


def test_project_run_changes_library_genies_and_the_tabs_follow(fx_install, monkeypatch):
    """Replacing fxnest.n0 over FxMain + FxLib: n1 open in a tab is changed there (not
    saved, not saved over by the helper); with n1 closed, the helper saves it, the chain
    saves n2 .. n4, and the window's open tabs are told what was saved."""
    from PySide6 import QtWidgets
    from ctgedit.editing import ElementDocument
    from ctgedit.ui import main_window as MW
    from ctgedit.ui.dialog import replace_genie as RG

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    lib = _nest(fx_install)
    before = open(lib, "rb").read()
    n1 = ElementDocument.open_element(lib, "n1")
    win = _Win(n1)
    monkeypatch.setattr(MW.MainWindow, "instances", [win])
    asked = []
    run = RG.ProjectReplaceRun(
        win._qobj,
        _projects(fx_install),
        [("fxnest", "n0")],
        ("fxgenie", "fx_value"),
        {"Description": "%Label%"},
        "topleft",
        confirm=lambda t, d: asked.append((t, d)) or True,
    )
    run.win = win
    _run_in_process(run)
    run.start()
    assert "in 1 library genie" in asked[0][0]
    assert "fxnest.n1 (open in the editor): 1" in asked[0][1]
    assert run.changed_open == ["fxnest.n1"] and run.changed_open_elements == ["fxnest.n1"]
    assert n1.dirty and [l for _i, l in n1.replaceable_genies()] == [("fxgenie", "fx_value")]
    assert open(lib, "rb").read() == before  # the tab's change is not saved
    assert "Saving a library genie brings what uses it up to date." in run.result_text

    # closed: the helper saves n1, the chain the genies that nest it; the tabs follow
    win2 = _Win()
    followed = []
    win2._follow_run_saves = lambda saved: followed.append(list(saved))
    monkeypatch.setattr(MW.MainWindow, "instances", [win2])
    run = RG.ProjectReplaceRun(
        win2._qobj,
        _projects(fx_install),
        [("fxnest", "n0")],
        ("fxgenie", "fx_value"),
        {"Description": "%Label%"},
        "topleft",
        confirm=lambda t, d: True,
    )
    run.win = win2
    _run_in_process(run)
    run.start()
    assert run.answer["elements_updated"] == ["fxnest.n1"]
    assert followed and "fxnest.n1" in followed[0] and "fxnest.n4" in followed[0]
    assert "Library genies saved: fxnest.n1." in run.result_text
    assert "Brought up to date after them:" in run.result_text
    assert "FxMembers" in run.result_text  # left: not registered


def test_the_dialog_works_in_a_library_genie_window(fx_install):
    from PySide6 import QtWidgets
    from ctgedit.editing import ElementDocument
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    n1 = ElementDocument.open_element(_nest(fx_install), "n1")
    dlg = ReplaceGenieDialog(n1)
    assert dlg.old_genies() == [("fxnest", "n0")] and dlg.targets()
    assert dlg.cb_project.currentText() == "FxLib"  # the library's project
    dlg.new_field.set_value(("fxgenie", "fx_value"), None, emit=True)
    rows = {dlg.table.item(r, 0).text(): r for r in range(dlg.table.rowCount())}
    dlg._value_box(rows["Description"]).setCurrentText("%Label%")
    assert dlg.ok.isEnabled() and dlg.mapping() == {"Description": "%Label%"}
    assert "Old Label" in dlg.table.item(rows["Description"], 2).text()
    dlg.deleteLater()


def test_pages_and_library_genies_supplied_with_plant_scada_are_left(fx_install, monkeypatch):
    """A project supplied with Plant SCADA (template_place.SHIPPED_PROJECTS: SA_Include,
    SA_Library ...) is never written: its pages and library genies are named as left."""
    from ctgedit.workers import genieswap_worker as GW
    from ctgedit.format import template_place

    monkeypatch.setattr(
        template_place, "SHIPPED_PROJECTS", tuple(template_place.SHIPPED_PROJECTS) + ("FxMain", "FxLib")
    )
    res = GW.find({"projects": _projects(fx_install), "old": [["fxnest", "n0"]]})
    assert not res["pages"] and not res["elements"]
    assert "fxnest.n1" in res["shipped"]
    res = GW.find({"projects": _projects(fx_install), "old": [["fxgenie", "fx_pump"]]})
    assert not res["pages"] and "FxMain\\FxBig" in res["shipped"]
    page = fx_install.page("FxBig", "FxMain")
    before = open(page, "rb").read()
    out = GW.apply(
        {
            "pages": [page],
            "elements": [],
            "old": [["fxgenie", "fx_pump"]],
            "new": ["fxgenie", "fx_lamp"],
            "mapping": {},
            "builder_open": [],
            "backup": None,
        }
    )
    assert not out["updated"] and "supplied with Plant SCADA" in out["failed"][0]
    assert open(page, "rb").read() == before


# ------------------------------------------------------------------ several old genies at once
def test_several_old_genies_become_one_new_genie(fx_install):
    """fx_pump (Tag, Label) and fx_valve (TagPrefix) both become fx_lamp (Tag) in one step:
    'Tag' = '%Tag%%TagPrefix%' gives each its own value (the one it lacks is '': the
    old_params of every old genie), one undo step puts both back."""
    doc = _open(fx_install)
    p = doc.place_library_element(
        "fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "PUMP_1", "Label": "L"}
    )
    v = doc.place_library_element("fxgenie", "fx_valve", "ctm", (500, 200), params={"TagPrefix": "VALVE_1"})
    before = [writer.object_payload(o) for o in doc.parsed.page.objects]
    n = doc.replace_genies(
        [p, v], "fxgenie", "fx_lamp", {"Tag": "%Tag%%TagPrefix%"}, old_params=["Tag", "Label", "TagPrefix"]
    )
    assert n == 2
    a, b = doc.replaced_roots
    assert _link(doc, a) == _link(doc, b) == ("fxgenie", "fx_lamp")
    assert dict(doc.genie_instance_bindings(a)) == {"Tag": "PUMP_1"}
    assert dict(doc.genie_instance_bindings(b)) == {"Tag": "VALVE_1"}
    doc.undo()
    assert [writer.object_payload(o) for o in doc.parsed.page.objects] == before


def test_a_token_no_old_genie_has_is_kept_as_written(fx_install):
    """A '%Name%' that is no parameter of any old genie stays as it is (in a library genie
    it names the outer genie's parameter)."""
    doc = _open(fx_install)
    p = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "PUMP_1"})
    doc.replace_genies([p], "fxgenie", "fx_lamp", {"Tag": "%Outer%_%Tag%"}, old_params=["Tag", "Label"])
    assert dict(doc.genie_instance_bindings(doc.replaced_roots[0])) == {"Tag": "%Outer%_PUMP_1"}


def test_dialog_ticks_the_selected_genies_and_offers_all_their_parameters(fx_install):
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = _open(fx_install)
    p = doc.place_library_element("fxgenie", "fx_pump", "ctm", (300, 200), params={"Tag": "PUMP_1"})
    v = doc.place_library_element("fxgenie", "fx_valve", "ctm", (500, 200), params={"TagPrefix": "VALVE_1"})
    dlg = ReplaceGenieDialog(doc, [p, v])
    assert set(dlg.old_genies()) == {("fxgenie", "fx_pump"), ("fxgenie", "fx_valve")}
    assert dlg.rb_selected.isChecked() and dlg.targets() == sorted([p, v])
    assert {"Tag", "Label", "TagPrefix"} <= set(dlg.old_params())
    dlg.new_field.set_value(("fxgenie", "fx_valve"), None, emit=True)
    assert not dlg.ok.isEnabled() and "one of those ticked" in dlg.status.text()
    dlg.new_field.set_value(("fxgenie", "fx_lamp"), None, emit=True)
    assert dlg.ok.isEnabled() and dlg.mapping() == {"Tag": "%Tag%"}
    assert "TagPrefix" in dlg.dropped.text()  # not used by any row
    # untick the valve: only the pump is left, its parameters only
    dlg.set_old_genies([("fxgenie", "fx_pump")])
    assert dlg.targets() == [p] and "TagPrefix" not in dlg.old_params()
    dlg.set_old_genies([])
    assert not dlg.ok.isEnabled() and "Tick the genies" in dlg.status.text()
    # every one here: all of the ticked kinds on the page
    dlg.set_old_genies([("fxgenie", "fx_pump"), ("fxgenie", "fx_valve")])
    dlg.rb_every.setChecked(True)
    pumps_valves = {
        i for i, l in doc.replaceable_genies() if l in (("fxgenie", "fx_pump"), ("fxgenie", "fx_valve"))
    }
    assert set(dlg.targets()) == pumps_valves
    dlg.deleteLater()


def test_the_worker_replaces_several_old_genies(fx_install):
    from ctgedit.workers import genieswap_worker as GW
    from ctgedit.editing import EditableDocument

    old = [["fxgenie", "fx_pump"], ["fxgenie", "fx_valve"]]
    found = GW.find({"projects": [fx_install.project("FxMain")], "old": old})
    names = {p["name"] for p in found["pages"]}
    assert "FxGenies" in names  # places both
    assert "FxMany" not in names  # (not registered: as Find / Replace leaves it)
    assert "FxBig" in names  # places the pump only
    res = GW.apply(
        {
            "pages": [p["page"] for p in found["pages"]],
            "old": old,
            "new": ["fxgenie", "fx_lamp"],
            "mapping": {"Tag": "%Tag%%TagPrefix%"},
            "old_params": found["params"],
            "builder_open": [],
            "backup": None,
        }
    )
    assert not res["failed"], res["failed"]
    assert res["changed"] == sum(p["count"] for p in found["pages"])
    links = [l for _i, l in EditableDocument.open(fx_install.page("FxGenies", "FxMain")).genie_instances()]
    assert ("fxgenie", "fx_pump") not in links and ("fxgenie", "fx_valve") not in links


# ------------------------------------------------------------------ genies not placed here
def _tree_rows(cl):
    """{'lib.element': (shown, Here column)} of a GenieChecklist."""
    out = {}
    for it in cl._genie_items():
        l, e = it.data(0, cl.LINK_ROLE)
        out["%s.%s" % (l.lower(), e.lower())] = (not it.isHidden(), it.text(1))
    return out


def test_the_checklist_lists_every_genie_the_project_can_place(fx_install):
    """Like Update Pages' tree: the genies of the page's include chain, grouped by library,
    with how many are placed here; 'Placed here only' (on when some are) and the filter
    choose the rows shown; Tick Shown ticks the rows shown, Untick All unticks all."""
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = _open(fx_install)  # FxBig: fx_pump genies only
    pumps = len([1 for _i, l in doc.replaceable_genies() if l == ("fxgenie", "fx_pump")])
    dlg = ReplaceGenieDialog(doc)
    cl = dlg.checklist
    rows = _tree_rows(cl)
    assert pumps and rows["fxgenie.fx_pump"] == (True, str(pumps))
    assert rows["fxgenie.fx_lamp"][1] == ""  # in the library, not placed here
    assert rows["fxnest.n2"]  # another library of the chain
    assert cl.here_only.isChecked() and not rows["fxgenie.fx_lamp"][0]
    # (the one genie placed here is ticked at first)
    assert [(l.lower(), e.lower()) for l, e in dlg.old_genies()] == [("fxgenie", "fx_pump")]
    cl.here_only.setChecked(False)
    assert _tree_rows(cl)["fxgenie.fx_lamp"][0]
    cl.tick_all(False, shown_only=False)
    cl.filter.setText("fx_va")
    shown = {k for k, (s, _n) in _tree_rows(cl).items() if s}
    assert shown and all("fx_va" in k for k in shown)
    cl.tick_all(True)
    assert {"%s.%s" % (l.lower(), e.lower()) for l, e in dlg.old_genies()} == shown
    cl.filter.setText("")
    cl.tick_all(False, shown_only=False)
    assert dlg.old_genies() == []
    dlg.deleteLater()


def test_ticking_a_genie_not_placed_here_replaces_it_project_wide(fx_install):
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = _open(fx_install)
    dlg = ReplaceGenieDialog(doc)
    dlg.set_old_genies([("fxgenie", "fx_valve")])  # not on FxBig
    assert dlg.rb_project.isChecked() and not dlg.rb_every.isEnabled()
    assert dlg.targets() == []
    assert "TagPrefix" in dlg.old_params()  # its declared parameters
    dlg.new_field.set_value(("fxgenie", "fx_lamp"), None, emit=True)
    assert dlg.ok.isEnabled(), dlg.status.text()
    # back to this page only: nothing here to replace
    dlg.rb_every.setEnabled(True)
    dlg.rb_every.setChecked(True)
    assert not dlg.ok.isEnabled() and "placed here" in dlg.status.text()
    dlg.deleteLater()


def test_a_page_with_no_genie_still_opens_the_dialog(fx_install):
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = EditableDocument.open(fx_install.page("FxShapes", "FxMain"))
    assert not doc.replaceable_genies()
    dlg = ReplaceGenieDialog(doc)
    assert not dlg.checklist.here_only.isChecked()  # (nothing here: all listed)
    assert "fxgenie.fx_pump" in _tree_rows(dlg.checklist)
    dlg.set_old_genies([("fxgenie", "fx_pump")])
    assert dlg.rb_project.isChecked()
    dlg.deleteLater()


def test_a_genie_whose_library_is_missing_is_listed_as_not_found(fx_install):
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = EditableDocument.open(fx_install.page("FxMissing", "FxMain"))
    if not any(l[0] == "fxgone" for _i, l in doc.replaceable_genies()):
        pytest.skip("FxMissing holds no replaceable fxgone genie")
    dlg = ReplaceGenieDialog(doc)
    cl = dlg.checklist
    libs = [cl.tree.topLevelItem(k).text(0) for k in range(cl.tree.topLevelItemCount())]
    assert "fxgone (not found)" in libs
    dlg.deleteLater()


def _slash_genie(fx):
    """A genie fxslash.dir with the parameters 'up/down1' and 'Tag' (a text showing both),
    in FxLib; the library is made here."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = os.path.join(fx.project("FxLib"), "fxslash.ctm")
    d = ElementDocument.new_element(lib, "dir", "genie")
    d.create_object("text", (0, 0, 120, 16), text="%up/down1% %Tag%")
    d.save()
    libraries_changed()
    return lib


def test_a_parameter_name_with_slashes_carries_its_value(fx_install):
    """Both ways: the old genie's 'up/down1' into the new genie's Tag, and the old Tag into
    the new genie's 'up/down1' (each instance its own value)."""
    _slash_genie(fx_install)
    doc = _open(fx_install)
    assert "up/down1" in doc.genie_parameters("fxslash", "dir")
    a = doc.place_library_element("fxslash", "dir", "ctm", (300, 200), params={"up/down1": "3", "Tag": "T1"})
    b = doc.place_library_element("fxslash", "dir", "ctm", (500, 200), params={"up/down1": "4", "Tag": "T2"})
    doc.replace_genies(
        [a, b], "fxgenie", "fx_lamp", {"Tag": "%up/down1%_%Tag%"}, old_params=["up/down1", "Tag"]
    )
    got = [dict(doc.genie_instance_bindings(i)) for i in doc.replaced_roots]
    assert got == [{"Tag": "3_T1"}, {"Tag": "4_T2"}]
    # and back: fx_lamp's Tag into the slash genie's parameter
    lamps = list(doc.replaced_roots)
    doc.replace_genies(lamps, "fxslash", "dir", {"up/down1": "%Tag%", "Tag": "X"}, old_params=["Tag"])
    got = [dict(doc.genie_instance_bindings(i)) for i in doc.replaced_roots]
    assert [g.get("up/down1") for g in got] == ["3_T1", "4_T2"]
    assert all(g.get("Tag") == "X" for g in got)


def test_the_dialog_suggests_a_slash_name_and_says_where_it_comes_from(fx_install):
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    _slash_genie(fx_install)
    doc = _open(fx_install)
    a = doc.place_library_element("fxslash", "dir", "ctm", (300, 200), params={"up/down1": "3", "Tag": "T1"})
    dlg = ReplaceGenieDialog(doc, [a])
    dlg.new_field.set_value(("fxgenie", "fx_lamp"), None, emit=True)
    rows = {dlg.table.item(r, 0).text(): r for r in range(dlg.table.rowCount())}
    dlg._value_box(rows["Tag"]).setCurrentText("%up/down1%")
    assert dlg.table.item(rows["Tag"], 2).text() == "Old up/down1"
    assert "up/down1" not in dlg.dropped.text() and "Tag" in dlg.dropped.text()
    dlg.deleteLater()
