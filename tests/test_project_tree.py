"""Project tree and flat views."""

import os
import shutil
import struct

import pytest

import fxinstall
from ctgedit.ui.browser.project_tree import CountRole, LabelRole
from qtkit import app as _app

#: a small symbol library of the fake install (FxLib's fxsym.ctl: fx_box, fx_round ...),
#: read from the committed fixtures and copied into the made-up projects
_SMALL_LIB = os.path.join(fxinstall.FX_INSTALL, "User", "FxLib", "fxsym.ctl")


def _write_dbf(path, fields, rows):
    """A minimal dBASE III table (character fields only)."""
    fdescs = b""
    rec_size = 1
    for name, length in fields:
        fdescs += name.ljust(11, "\x00").encode("latin1")[:11]
        fdescs += b"C" + b"\x00" * 4 + bytes([length]) + b"\x00" * 15
        rec_size += length
    hdr_size = 32 + len(fdescs) + 1
    head = struct.pack("<BBBBIHH20x", 3, 26, 6, 10, len(rows), hdr_size, rec_size)
    body = b""
    for row in rows:
        rec = b" "
        for (_name, length), val in zip(fields, row):
            rec += val.encode("latin1").ljust(length)[:length]
        body += rec
    with open(path, "wb") as fh:
        fh.write(head + fdescs + b"\x0d" + body + b"\x1a")


#: project -> (include.dbf rows, pages)
_LAYOUT = {
    "R": (["P", "M", "H"], 2),
    "P": (["Q"], 3),
    "Q": (["H"], 1),
    "S": (["P", "Gone", "H"], 1),
    "X": (["Y"], 1),
    "Y": (["X"], 1),
    "H": ([], 1),
    "Include": ([], 1),
    "System": ([], 0),
}


@pytest.fixture()
def user(tmp_path):
    ud = tmp_path / "Plant SCADA Test" / "User"
    ud.mkdir(parents=True)
    rows = []
    for name, (incs, pages) in _LAYOUT.items():
        d = ud / name
        d.mkdir()
        rows.append((name, str(d)))
        if incs:
            _write_dbf(str(d / "include.dbf"), [("NAME", 32)], [(n,) for n in incs])
        for k in range(pages):
            (d / ("%s_page%d.ctg" % (name, k + 1))).write_bytes(b"\x00" * 16)
    rows.append(("Gone", str(ud / "Gone")))  # registered, folder missing
    _write_dbf(str(ud / "MASTER.DBF"), [("NAME", 32), ("PATH", 200)], rows)
    loose = ud / "Loose"  # a folder nobody registered
    loose.mkdir()
    (loose / "Loose_page.ctg").write_bytes(b"\x00" * 16)
    return str(ud)


@pytest.fixture()
def cat(user):
    from ctgedit.io.catalog import ProjectCatalog

    return ProjectCatalog(user, other_installs=False)


def _names(pairs):
    return [(p.name, st) for p, st in pairs]


# --------------------------------------------------------------------------- catalog
def test_top_level_and_include_children(cat):
    top = [p.name for p in cat.top_level()]
    # nobody includes Loose, R, S; X and Y only include each other: X stands for them
    assert top == ["Loose", "R", "S", "X"]
    assert _names(cat.include_children("R")) == [
        ("P", "ok"),
        ("M", "missing"),
        ("H", "ok"),
        ("Include", "implicit"),
        ("System", "implicit"),
    ]
    m = cat.include_children("R")[1][0]
    assert not m.exists and not m.registered  # no MASTER.DBF row
    s = _names(cat.include_children("S"))
    assert s[:3] == [("P", "ok"), ("Gone", "missing"), ("H", "ok")]
    gone = cat.include_children("S")[1][0]
    assert gone.registered and not gone.exists  # registered, folder missing
    # below the top level: no implicit rows; a project on the path is a cycle
    assert _names(cat.include_children("P", path=[cat.project("R")])) == [("Q", "ok")]
    x, y = cat.project("X"), cat.project("Y")
    assert _names(cat.include_children(y, path=[x])) == [("X", "cycle")]
    # the implicit projects themselves are no top-level rows, and roots() is unchanged
    assert {p.name for p in cat.roots()} == {"R", "S"}
    # projects(existing_only=False) keeps the registered row whose folder is gone
    assert "Gone" in [p.name for p in cat.projects(existing_only=False)]
    assert "Gone" not in [p.name for p in cat.projects()]


def test_pages_elsewhere_covers_siblings_under_one_root(cat):
    # H is compiled in by R and S; R also compiles P and Q: Q's page is 'elsewhere' for H
    other = cat.pages_elsewhere("H")
    assert other.get("q_page1") == "Q" and other.get("r_page1") == "R"
    assert "h_page1" not in other


def test_entries_shadow_only_along_a_chain(cat):
    from ctgedit.format.pagesummary import list_element_names

    for proj in ("P", "Q"):
        shutil.copyfile(_SMALL_LIB, os.path.join(cat.project(proj).dir, "fxsym.ctl"))
    names = list_element_names(os.path.join(cat.project("P").dir, "fxsym.ctl"))
    assert names
    chain = cat.entries(cat.closure("P"), "symbol")
    q_copies = [e for e in chain if e.project == "Q"]
    assert q_copies and all(e.shadowed_by == "P" for e in q_copies)
    flat = cat.entries([cat.project("P"), cat.project("Q")], "symbol", shadow=False)
    assert len(flat) == len(chain) and not any(e.shadowed_by for e in flat)


# --------------------------------------------------------------------------- tree view
def _tree(cat, **kw):
    _app()
    from ctgedit.ui.browser.project_tree import ProjectTree

    return ProjectTree(cat, **kw)


def _kids(item):
    from ctgedit.ui.browser.project_tree import LabelRole, NoteRole

    return [
        (item.child(k).data(0, LabelRole), item.child(k).data(0, NoteRole) or "")
        for k in range(item.childCount())
    ]


def _install_row(tree):
    from ctgedit.ui.browser.project_tree import ScopeRole

    for k in range(tree.topLevelItemCount()):
        it = tree.topLevelItem(k)
        sc = it.data(0, ScopeRole)
        if sc is not None and sc.kind == "install":
            return it
    raise AssertionError("no install row")


def _child(item, label):
    from ctgedit.ui.browser.project_tree import LabelRole

    for k in range(item.childCount()):
        if item.child(k).data(0, LabelRole) == label:
            return item.child(k)
    raise AssertionError((label, _kids(item)))


def test_tree_nests_projects_under_their_includers(cat):
    t = _tree(cat, view="tree")
    inst = _install_row(t)
    assert inst.data(0, LabelRole) == "Active install: Test"  # LabelRole
    assert [k[0] for k in _kids(inst)] == ["Loose", "R", "S", "X"]
    r = _child(inst, "R")
    r.setExpanded(True)  # rows are made when first expanded
    assert _kids(r) == [
        ("P", ""),
        ("M", "(not found)"),
        ("H", ""),
        ("Include", "always included"),
        ("System", "always included"),
    ]
    p = _child(r, "P")
    p.setExpanded(True)
    assert _kids(p) == [("Q", "")]
    # a shared include appears under every project that includes it
    s = _child(inst, "S")
    s.setExpanded(True)
    assert [k[0] for k in _kids(s)] == ["P", "Gone", "H", "Include", "System"]
    sp = _child(s, "P")
    sp.setExpanded(True)
    assert _kids(sp) == [("Q", "")]
    # a project already on the path is a dim leaf
    x = _child(inst, "X")
    x.setExpanded(True)
    y = _child(x, "Y")
    y.setExpanded(True)
    assert _kids(y) == [("X", "(included above)")]
    leaf = _child(y, "X")
    assert leaf.childCount() == 0
    t.deleteLater()


def test_missing_rows_are_dim_and_cannot_be_selected(cat):
    from PySide6 import QtCore

    t = _tree(cat, view="tree")
    inst = _install_row(t)
    r = _child(inst, "R")
    r.setExpanded(True)
    m = _child(r, "M")
    assert not (m.flags() & QtCore.Qt.ItemIsSelectable)
    assert "In include.dbf, project not found" in m.toolTip(0)
    s = _child(inst, "S")
    s.setExpanded(True)
    gone = _child(s, "Gone")
    assert not (gone.flags() & QtCore.Qt.ItemIsSelectable)
    assert "Registered, folder missing" in gone.toolTip(0)
    t.setCurrentItem(m)
    assert t.current_scope() is None or t.current_scope().anchor is not None
    t.deleteLater()


def test_install_row_is_a_scope_with_every_project(cat):
    t = _tree(cat, view="tree")
    got = []
    t.scopeChanged.connect(got.append)
    assert t.select_scope_id(t.install_scope_id())
    s = got[-1]
    assert s.kind == "install" and s.label == "All projects in Test"
    assert {p.name for p in s.projects} == {p.name for p in cat.projects()}
    inst = _install_row(t)
    total = sum(n for _i, n in _LAYOUT.values()) + 1  # + Loose's page
    assert inst.data(0, CountRole) == total  # pages
    assert "Test" in t.catalog.live_install.short_label
    t.deleteLater()


def test_flat_view_lists_every_project_once(cat):
    from ctgedit.ui.browser.project_tree import NoteRole

    t = _tree(cat, view="flat")
    inst = _install_row(t)
    labels = [k[0] for k in _kids(inst)]
    assert labels == sorted(labels, key=str.lower)
    assert sorted(labels) == sorted(["Gone", "H", "Include", "Loose", "P", "Q", "R", "S", "System", "X", "Y"])
    notes = dict(_kids(inst))
    assert notes["P"] == "included by R, S"
    assert notes["H"].startswith("included by ") and notes["H"].endswith(" +1")
    h = _child(inst, "H")
    assert all(n in h.toolTip(0) for n in ("R", "S", "Q"))
    assert notes["Include"] == "always included"
    assert notes["Gone"] == "(not found)"
    assert all(_child(inst, n).childCount() == 0 for n in labels)
    assert h.data(0, NoteRole) == notes["H"]
    t.deleteLater()


def test_select_project_picks_the_first_row_in_tree_order(cat):
    t = _tree(cat, view="tree")
    got = []
    t.scopeChanged.connect(got.append)
    assert t.select_project(cat.project("Q"))
    item = t.currentItem()
    path = []
    while item is not None:
        path.append(item.data(0, LabelRole))
        item = item.parent()
    assert list(reversed(path)) == ["Active install: Test", "R", "P", "Q"]
    assert got[-1].kind == "project" and got[-1].anchor.name == "Q"
    # with includes: the project's chain, and the id says so
    assert t.select_project(cat.project("P"), chain=True)
    assert t.with_includes() and got[-1].kind == "chain"
    assert [p.name for p in got[-1].projects] == [p.name for p in cat.closure("P")]
    t.deleteLater()


def test_a_picked_row_of_a_shared_include_stays_picked(cat):
    """H is listed under R, under Q and under S: the row the user picked stays
    the selected one when its scope is selected again and after a rebuild."""
    t = _tree(cat, view="tree")
    r = _child(_install_row(t), "R")
    r.setExpanded(True)
    h = _child(r, "H")  # not the first H in tree order (R/P/Q/H)
    t.setCurrentItem(h)
    sid = t.current_base_scope().id
    assert t.select_scope_id(sid)
    assert t.currentItem() is h
    t.rebuild()
    item, path = t.currentItem(), []
    while item is not None:
        path.append(item.data(0, LabelRole))
        item = item.parent()
    assert list(reversed(path)) == ["Active install: Test", "R", "H"]
    t.deleteLater()


def test_expanded_rows_survive_a_rebuild(cat):
    t = _tree(cat, view="tree")
    inst = _install_row(t)
    r = _child(inst, "R")
    r.setExpanded(True)
    _child(r, "P").setExpanded(True)
    paths = t.expanded_paths()
    assert "Active install: Test/R/P" in paths
    t.rebuild()
    inst = _install_row(t)
    r = _child(inst, "R")
    assert r.isExpanded() and _child(r, "P").isExpanded()
    t2 = _tree(cat, view="tree")
    t2.restore_expanded(paths)
    assert _child(_child(_install_row(t2), "R"), "P").isExpanded()
    t.deleteLater()
    t2.deleteLater()


def test_chain_tree_of_a_picker(cat):
    """With chain_of the tree holds the project and its chain only."""
    t = _tree(cat, view="tree", chain_of=cat.project("R"), with_includes=True)
    assert t.topLevelItemCount() == 1
    top = t.topLevelItem(0)
    assert top.data(0, LabelRole) == "R" and top.isExpanded()
    assert [k[0] for k in _kids(top)] == ["P", "M", "H", "Include", "System"]
    t.set_view("flat")
    labels = [t.topLevelItem(k).data(0, LabelRole) for k in range(t.topLevelItemCount())]
    assert labels == [p.name for p in cat.closure("R")]
    t.deleteLater()


def test_browser_on_the_made_up_install(cat, user):
    """The page browser on this User folder: the install row lists every
    project's pages; Tree / Flat buttons switch the shared view."""
    _app()
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import app_settings

    start = os.path.join(user, "Q", "Q_page1.ctg")
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=cat, start_path=start, kinds=("page",))
    try:
        assert dlg.current_scope().anchor.name == "Q"
        assert dlg.tree.view() == "tree" and dlg.btn_tree.isChecked()
        assert dlg.tree.select_scope_id(dlg.tree.install_scope_id())
        assert dlg.model.total == sum(n for _i, n in _LAYOUT.values()) + 1
        assert {e.project for e in dlg.model.all_entries()} >= {"R", "P", "Q", "Loose"}
        dlg.btn_flat.click()
        assert dlg.tree.view() == "flat" and dlg.btn_flat.isChecked()
        assert str(app_settings().value("pagebrowser/project_view")) == "flat"
        assert dlg.current_scope().kind == "install"  # the selection is kept
    finally:
        dlg.reject()
        dlg.deleteLater()
