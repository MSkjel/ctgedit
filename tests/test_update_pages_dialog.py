"""The Update Pages dialog."""

from ctgedit.ui.main_window import MainWindow
import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_update_pages_parts import tmp_install  # noqa: E402


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _dlg(user, start=None):
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog

    return UpdatePagesDialog(start, None, projects=[(n, os.path.join(user, n)) for n in ("Root", "A", "B")])


def _rows(dlg):
    """(name, pages text, depth, tickable, ticked) of each row, in tree order."""
    out = []
    it = QtWidgets.QTreeWidgetItemIterator(dlg._tree)
    while it.value() is not None:
        item = it.value()
        depth, p = 0, item.parent()
        while p is not None:
            depth, p = depth + 1, p.parent()
        out.append(
            (
                item.text(0),
                item.text(1),
                depth,
                bool(item.flags() & QtCore.Qt.ItemIsUserCheckable),
                item.checkState(0) == QtCore.Qt.Checked,
            )
        )
        it += 1
    return out


def _set_root(dlg, name):
    dlg._root.setCurrentIndex(dlg._root.findText(name))


def test_the_combo_lists_the_install_without_include_and_system(app, tmp_path, monkeypatch):
    from ctgedit.io import catalog
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog

    user = tmp_install(tmp_path)
    cat = catalog.ProjectCatalog(user, other_installs=False)
    monkeypatch.setattr(catalog, "catalog_for", lambda *a, **k: cat)
    dlg = UpdatePagesDialog(os.path.join(user, "A"), None)
    try:
        names = [dlg._root.itemText(k) for k in range(dlg._root.count())]
        assert names == ["A", "B", "Root"]
        assert dlg.root_name() == "A"  # (the open page's project)
        assert [r[0] for r in _rows(dlg)] == ["A", "B"]
    finally:
        dlg.deleteLater()


def test_a_root_without_pages_shows_its_include_tree(app, tmp_path):
    user = tmp_install(tmp_path)
    dlg = _dlg(user, os.path.join(user, "Root"))
    try:
        assert dlg.root_name() == "Root"
        assert _rows(dlg) == [
            ("Root", "No pages", 0, False, False),
            ("A", "3 pages", 1, True, True),
            ("B", "1 page", 2, True, True),
        ]
        assert dlg._count.text() == "4 pages in 2 projects will be checked."
        assert dlg._ok.isEnabled()
        assert dlg.projects() == [os.path.join(user, "A"), os.path.join(user, "B")]
        dlg._none.click()
        assert not dlg._ok.isEnabled() and dlg._count.text() == "No pages will be checked."
        assert dlg.projects() == []
        dlg._all.click()
        assert dlg._ok.isEnabled() and len(dlg.projects()) == 2
        _set_root(dlg, "A")  # (another root: another tree)
        assert [r[0] for r in _rows(dlg)] == ["A", "B"]
        assert dlg._count.text() == "4 pages in 2 projects will be checked."
    finally:
        dlg.deleteLater()


def test_the_ticks_are_remembered_per_root(app, tmp_path):
    from ctgedit.ui.prefs.settings import app_settings

    user = tmp_install(tmp_path)
    dlg = _dlg(user, os.path.join(user, "Root"))
    b = [it for it in dlg._tree.findItems("B", QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive)]
    b[0].setCheckState(0, QtCore.Qt.Unchecked)
    assert dlg._count.text() == "3 pages in 1 project will be checked."
    dlg.accept()
    dlg.deleteLater()
    st = app_settings()
    assert st.value("updatepages/root") == "Root"
    assert json.loads(st.value("updatepages/ticks/root")) == ["B"]
    dlg = _dlg(user)  # (nothing open: the last root)
    try:
        assert dlg.root_name() == "Root"
        assert [(r[0], r[4]) for r in _rows(dlg)] == [("Root", False), ("A", True), ("B", False)]
        _set_root(dlg, "A")  # (the ticks of another root)
        assert [(r[0], r[4]) for r in _rows(dlg)] == [("A", True), ("B", True)]
    finally:
        dlg.deleteLater()


def test_the_helper_processes_box(app, tmp_path):
    """The box's value is this run's count; it is kept as the setting when the run starts
    (OK), never on a step or on Cancel."""
    from ctgedit.ui.prefs.settings import app_settings

    user = tmp_install(tmp_path)
    st = app_settings()
    dlg = _dlg(user)
    try:
        assert dlg._procs.value() == 0 and dlg._procs.text() == "Automatic"
        assert dlg.processes() == 0
        dlg._procs.setValue(3)
        assert not st.contains("opt/update_processes")  # (a step writes nothing)
        assert dlg.processes() == 3
        dlg.reject()
        assert not st.contains("opt/update_processes")  # (Cancel: untouched)
        dlg._procs.setValue(3)
        dlg.accept()
        assert int(str(st.value("opt/update_processes"))) == 3
    finally:
        dlg.deleteLater()
    dlg = _dlg(user)
    try:
        assert dlg._procs.value() == 3
    finally:
        dlg.deleteLater()


def test_the_settings_row_writes_the_key(app):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    w = MainWindow()
    try:
        d = SettingsDialog(w)
        assert d._update_procs.text() == "Automatic"
        d._update_procs.setValue(5)
        d._store()
        assert int(str(app_settings().value("opt/update_processes"))) == 5
        assert SettingsDialog(w)._update_procs.value() == 5
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.close()
        w.deleteLater()


def test_browse_makes_any_folder_the_root_and_it_comes_back(app, tmp_path, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings

    user = tmp_install(tmp_path)
    loose = tmp_path / "Loose"
    loose.mkdir()
    told = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    dlg = _dlg(user)
    try:
        assert dlg.add_root(os.path.join(user, "A")) and dlg.root_name() == "A"
        assert dlg._root.count() == 3  # (a listed one is selected)
        assert not dlg.add_root(os.path.join(user, "Include")) and told
        assert dlg.root_name() == "A" and dlg._root.count() == 3
        assert dlg.add_root(str(loose)) and dlg.root_name() == "Loose"
        assert _rows(dlg) == [("Loose", "No pages", 0, False, False)]
        dlg.accept()
    finally:
        dlg.deleteLater()
    assert app_settings().value("updatepages/root_dir") == str(loose)
    dlg = _dlg(user)  # (the browsed root comes back)
    try:
        assert dlg.root_name() == "Loose" and dlg._root.count() == 4
        _set_root(dlg, "A")
        dlg.accept()
    finally:
        dlg.deleteLater()
    assert app_settings().value("updatepages/root_dir") == ""


def test_the_busy_project_waits_reach_the_locks_and_the_helpers(monkeypatch):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.format import composer_store, projectlock
    from ctgedit.ui.prefs import settings as S

    for k in ("project", "page", "file"):
        monkeypatch.setitem(projectlock._WAITS, k, projectlock._WAITS[k])
    monkeypatch.setattr(composer_store, "LOCK_WAIT", [composer_store.LOCK_WAIT[0]])
    monkeypatch.delenv("CTG_HELPER_WAIT", raising=False)
    assert S.lock_waits() == (10, 30, 120)
    st = S.app_settings()
    st.setValue("opt/wait_project", 7)
    st.setValue("opt/wait_page", 9)
    st.setValue("opt/wait_helpers", 99)
    S.apply_lock_waits()
    assert projectlock.waits() == (7.0, 9.0) and composer_store.LOCK_WAIT[0] == 7.0
    assert os.environ["CTG_HELPER_WAIT"] == "99"
    GW.helper_waits()  # (as a helper started now)
    assert projectlock.waits() == (99.0, 99.0) and composer_store.LOCK_WAIT[0] == 99.0
    monkeypatch.delenv("CTG_HELPER_WAIT")
    GW.helper_waits()  # (no setting passed down)
    assert projectlock.waits() == projectlock.HELPER_WAITS


def test_pages_each_helper_gets_at_least(monkeypatch):
    from ctgedit.ui.prefs import settings as S

    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    from ctgedit.infra import workers

    monkeypatch.setattr(workers, "free_memory_mb", lambda: None)
    assert S.update_parts(15, 4) == 1  # (8 a helper by default)
    assert S.update_parts(32, 4) == 4
    S.app_settings().setValue("opt/update_pages_per_helper", 2)
    assert S.update_parts(15, 4) == 4 and S.update_parts(5, 4) == 2
    S.app_settings().setValue("opt/update_pages_per_helper", "x")  # (garbled: the default)
    assert S.update_parts(15, 4) == 1


def test_with_a_library_element_open_the_root_includes_its_project(app, tmp_path, monkeypatch):
    """A template or genie in B: its pages are in the projects that include B (not B itself,
    whose tree misses them)."""
    from ctgedit.io import citect_config
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog

    monkeypatch.setattr(citect_config, "ini_value", lambda *a, **k: "")
    user = tmp_install(tmp_path)
    projects = [(n, os.path.join(user, n)) for n in ("Root", "A", "B")]
    dlg = UpdatePagesDialog(None, None, projects=projects, library_dir=os.path.join(user, "B"))
    try:
        assert dlg.root_name() == "A"  # (the first that includes B)
    finally:
        dlg.deleteLater()
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("updatepages/root", "Root")  # (the last root includes B too)
    dlg = UpdatePagesDialog(None, None, projects=projects, library_dir=os.path.join(user, "B"))
    try:
        assert dlg.root_name() == "Root"
    finally:
        dlg.deleteLater()
    dlg = UpdatePagesDialog(None, None, projects=projects, library_dir=os.path.join(user, "Root"))
    try:
        assert dlg.root_name() == "Root"  # (nobody includes it: its own)
    finally:
        dlg.deleteLater()


def test_a_browsed_copy_is_told_from_the_listed_project_of_its_name(app, tmp_path):
    from ctgedit.ui.prefs.settings import app_settings

    user = tmp_install(tmp_path)
    copy = tmp_path / "Copies" / "A"
    copy.mkdir(parents=True)
    dlg = _dlg(user)
    try:
        assert dlg.add_root(str(copy)) and dlg._root.count() == 4
        assert dlg._root.itemData(3, QtCore.Qt.ToolTipRole) == str(copy)
        key = dlg._ticks_key()
        assert key != "updatepages/ticks/a"
        dlg.accept()
    finally:
        dlg.deleteLater()
    dlg = _dlg(user)  # (the copy, not the listed A)
    try:
        assert dlg._root.currentData() == str(copy) and dlg._ticks_key() == key
        _set_root(dlg, "A")
        assert dlg._root.currentData() == os.path.join(user, "A")
        assert dlg._ticks_key() == "updatepages/ticks/a"
    finally:
        dlg.deleteLater()
    assert app_settings().value("updatepages/root_dir") == str(copy)


def test_no_projects_at_all_leaves_update_pages_off(app):
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog

    dlg = UpdatePagesDialog(None, None, projects=[])
    try:
        assert not dlg._ok.isEnabled() and dlg._count.text() == "No pages will be checked."
    finally:
        dlg.deleteLater()


def _long_install(tmp_path):
    """A root with a nested include tree of long project names."""
    from test_update_pages_parts import include_dbf, pages_dbf, write_dbf

    user = str(tmp_path / "Install" / "User")
    names = [
        "Plant_Root_Project",
        "Area_North_Mixing_And_Dosing",
        "Shared_Style_Include_Long",
        "Library_Equipment_Pumps_And_Valves",
        "Area_South_Packaging_Line_Two",
    ]
    for n in names:
        os.makedirs(os.path.join(user, n))
    write_dbf(
        os.path.join(user, "MASTER.DBF"),
        [("NAME", 64), ("PATH", 240)],
        [{"NAME": n, "PATH": os.path.join(user, n)} for n in names],
    )
    include_dbf(os.path.join(user, names[0]), [names[1], names[4]])
    include_dbf(os.path.join(user, names[1]), [names[2], names[3]])
    include_dbf(os.path.join(user, names[4]), [names[2]])
    for n, k in ((names[1], 12), (names[2], 3), (names[4], 140)):
        pages_dbf(os.path.join(user, n), ["p%d" % i for i in range(k)])
        for i in range(k):
            with open(os.path.join(user, n, "p%d.ctg" % i), "wb") as fh:
                fh.write(b"page")
    return user, names


def test_the_project_names_are_never_cut_while_there_is_room(app, tmp_path):
    """The tree has a header (Project, Pages) the user can size, the widths remembered;
    until then the names take what the page counts leave (Qt's own stretch of the last
    column is not a drag)."""
    from PySide6 import QtTest
    from ctgedit.ui import tables
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog
    from ctgedit.ui.prefs.settings import app_settings

    user, names = _long_install(tmp_path)
    dlg = UpdatePagesDialog(
        os.path.join(user, names[0]), None, projects=[(n, os.path.join(user, n)) for n in names]
    )
    try:
        dlg.show()
        for _ in range(20):
            app.processEvents()
        tree = dlg._tree
        hdr = tree.header()
        assert not hdr.isHidden()
        assert [tree.headerItem().text(c) for c in range(2)] == ["Project", "Pages"]
        assert not tables.has_user_widths(tree)
        width = tree.viewport().width()
        if width >= tree.sizeHintForColumn(0) + tree.sizeHintForColumn(1):
            assert hdr.sectionSize(0) >= tree.sizeHintForColumn(0)  # no name is cut
        # (the names take what the page counts leave)
        assert hdr.sectionSize(0) + hdr.sectionSize(1) >= width - 2
        assert hdr.sectionSize(1) >= tree.fontMetrics().horizontalAdvance("140 pages")
        assert hdr.sectionSize(1) < width // 3  # (as wide as its text)
        assert hdr.sectionSize(0) + hdr.sectionSize(1) <= width + 2
        assert tree.horizontalScrollBar().maximum() == 0
        shot = str(tmp_path / "update_pages.png")
        assert dlg.grab().save(shot) and os.path.getsize(shot) > 0
        # the user drags the divider: the widths are remembered
        vp = hdr.viewport()
        x = hdr.sectionViewportPosition(0) + hdr.sectionSize(0) - 1
        y = vp.height() // 2
        QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
        QtTest.QTest.mouseMove(vp, QtCore.QPoint(x - 40, y))
        QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x - 40, y))
        for _ in range(5):
            app.processEvents()
        assert tables.has_user_widths(tree)
        mine = hdr.sectionSize(1)
        assert app_settings().value(tables.settings_key("UpdatePagesDialog/projects"))
    finally:
        dlg.close()
        dlg.deleteLater()
    dlg = UpdatePagesDialog(
        os.path.join(user, names[0]), None, projects=[(n, os.path.join(user, n)) for n in names]
    )
    try:
        dlg.show()
        for _ in range(20):
            app.processEvents()
        assert dlg._tree.header().sectionSize(1) == mine
    finally:
        dlg.close()
        dlg.deleteLater()


def test_the_summary_names_the_pages_closed_during_the_run():
    import types

    rep = types.SimpleNamespace(
        pages=3,
        projects=["A"],
        stale=1,
        updated=[],
        stopped=False,
        symbols=0,
        templates=0,
        supergenies=0,
        instances=0,
        composites=0,
        composites_skipped=0,
        failed=[],
        unregistered=[],
        open_in_builder=[],
        skipped_open=[],
        changed_meanwhile=[],
        db_in_use=[],
        rows_pending=[],
        rows_written=[],
        warnings=[],
        not_checked=0,
        errors=[],
        recovered=[],
        records=[],
        closed_meanwhile=["Mixer"],
    )
    lines = MainWindow._update_pages_summary(rep, None, (0, 0, 0, 0))
    assert any("closed before it ended" in x and "Mixer" in x for x in lines)


def test_the_summary_explains_every_out_of_date_page_in_plain_lines():
    """The box accounts for every out-of-date page (the counts, then a line for each kind
    that was not simply updated: links removed, unchanged, open in tabs, failed); a file is
    '<project>\\<file>' (the log keeps the full path); a list past 10 ends 'and N more';
    every line ends in one full stop."""
    import types
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate

    root = r"C:\Install\User"
    many = ["p%02d" % i for i in range(12)]
    recs = [{"path": os.path.join(root, "A", n + ".ctg")} for n in many + ["bad", "q"]]
    rep = types.SimpleNamespace(
        pages=14,
        projects=["A"],
        stale=14,
        updated=many,
        stopped=False,
        symbols=0,
        templates=0,
        supergenies=0,
        instances=12,
        composites=0,
        composites_skipped=0,
        failed=["bad: [Errno 13] Permission denied: '%s'" % os.path.join(root, "A", "bad.ctg")],
        unregistered=[],
        open_in_builder=["q"],
        skipped_open=[],
        changed_meanwhile=[],
        db_in_use=[],
        rows_pending=[],
        rows_written=[],
        warnings=["p00: the fast display of %s was kept" % os.path.join(root, "A", "p00.ctF")],
        not_checked=0,
        errors=[],
        recovered=[],
        records=recs,
        links_dropped=many,
        unchanged=["u1"],
    )
    tabs = OpenTabsUpdate(
        updated=[os.path.join(root, "A", "t1.ctg")],
        failed=[os.path.join(root, "A", "t2.ctg")],
        nothing=[os.path.join(root, "A", "t3.ctg")],
    )
    lines = MainWindow._update_pages_summary(rep, None, tabs)
    assert (
        lines[0] == "17 pages checked in 1 project, 16 out of date, 13 updated (1 in its tab, not saved yet)."
    )
    want = [
        "Links to library elements no longer on the page removed: p00, p01, p02, p03, p04, "
        "p05, p06, "
        "p07, p08, p09 and 2 more.",
        "Out of date, but nothing on them could be updated (the log says why): u1.",
        "Open in a tab and updated there (save it to keep the changes): t1.",
        "Open in a tab, nothing to update: t3.",
        "Open in a tab, could not be updated (the log says why): t2.",
        "Skipped because they are open in the Graphics Builder (close them there and run "
        "Update Pages again): q.",
    ]
    for x in want:
        assert x in lines, (x, lines)
    text = "\n".join(lines)
    assert "Permission denied: 'A\\bad.ctg'." in text and root not in text
    assert "the fast display of A\\p00.ctF was kept." in text
    assert "\u2026" not in text and ".." not in text
    for x in lines:
        for line in x.strip("\n").split("\n"):
            assert line.endswith((".", ":")), line


def test_the_summary_counts_a_project_once_whatever_its_case():
    """A tab opened from a path spelled '...\\fxmain\\t3.ctg' is in the run's 'FxMain':
    one project, the names stay plain. A tab out of
    date with nothing changed has its own line and counts out of date, as a closed one."""
    import types
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate

    root = r"C:\Install\User"
    rep = types.SimpleNamespace(
        pages=1,
        projects=["FxMain"],
        stale=0,
        updated=[],
        stopped=False,
        symbols=0,
        templates=0,
        supergenies=0,
        instances=0,
        composites=0,
        composites_skipped=0,
        failed=[],
        unregistered=[],
        open_in_builder=[],
        skipped_open=[],
        changed_meanwhile=[],
        db_in_use=[],
        rows_pending=[],
        rows_written=[],
        warnings=[],
        not_checked=0,
        errors=[],
        recovered=[],
        records=[{"path": os.path.join(root, "FxMain", "a.ctg")}],
        links_dropped=[],
        unchanged=[],
    )
    tabs = OpenTabsUpdate(
        updated=[os.path.join(root, "fxmain", "T3.ctg")], unchanged=[os.path.join(root, "FXMAIN", "T4.ctg")]
    )
    assert tabs.projects() == ["fxmain"] and tabs.stale == 2
    lines = MainWindow._update_pages_summary(rep, None, tabs)
    assert (
        lines[0] == "3 pages checked in 1 project, 2 out of date, 1 updated (1 in its tab, not saved yet)."
    ), lines
    assert "Open in a tab and updated there (save it to keep the changes): T3." in lines
    assert "Open in a tab, out of date, but nothing on it could be updated (the log says why): T4." in lines


def test_old_run_folders_of_ended_editors_are_swept():
    from ctgedit.infra.paths import data_dir
    from ctgedit.ui.jobs.update_run import sweep_old_runs

    dead = data_dir("updatepages", "4194300-1")
    mine = data_dir("updatepages", "%d-2" % os.getpid())
    other = data_dir("updatepages", "notes")
    for d in (dead, mine, other):
        os.makedirs(os.path.join(d, "claims"), exist_ok=True)
    assert sweep_old_runs() == 1
    assert not os.path.exists(dead) and os.path.isdir(mine) and os.path.isdir(other)


def test_the_choices_of_the_run_come_before_the_project_list(app, tmp_path):
    """What the run updates, the backup and the helpers sit above the project list, and Tab goes
    down the window in that order."""
    user = tmp_install(tmp_path)
    dlg = _dlg(user, os.path.join(user, "Root"))
    try:
        dlg.show()
        app.processEvents()
        what = next(b for b in dlg.findChildren(QtWidgets.QGroupBox) if b.title() == "Update")
        projects = next(b for b in dlg.findChildren(QtWidgets.QGroupBox) if b.title() == "Projects")
        top = lambda w: w.mapTo(dlg, QtCore.QPoint(0, 0)).y()  # noqa: E731
        assert top(what) < top(dlg._backup) < top(projects)
        # (the spin box's editor is its child: the step after it is the project combo)
        chain, w = [], dlg._backup
        for _k in range(6):
            w = w.nextInFocusChain()
            chain.append(w)
        assert dlg._root in chain
    finally:
        dlg.deleteLater()
