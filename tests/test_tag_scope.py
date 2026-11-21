"""A page's tag scope."""

import datetime
import os
import shutil
import struct
import time

import pytest

import fxinstall
from ctgedit.format.dbf import Dbf
from ctgedit.io import project as PR
from ctgedit.io import tagscope as TS

heavy = pytest.mark.skipif(os.environ.get("CTG_SKIP_HEAVY") == "1", reason="CTG_SKIP_HEAVY")

VAR_FIELDS = [
    ("NAME", 79),
    ("TYPE", 16),
    ("COMMENT", 48),
    ("ENG_ZERO", 10),
    ("ENG_FULL", 10),
    ("ENG_UNITS", 8),
    ("EQUIP", 64),
    ("ITEM", 64),
    ("FORMAT", 16),
]


# ------------------------------------------------------------------ a small install
def write_dbf(path, fields, rows):
    """A dBASE III table of char ``fields`` [(name, length)]: the header is
    written here, the rows through format.dbf (Dbf.encode / append)."""
    desc = b"".join(
        n.encode("ascii").ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([ln]) + b"\0" * 15 for n, ln in fields
    )
    hlen = 32 + len(desc) + 1
    rlen = 1 + sum(ln for _n, ln in fields)
    d = datetime.date.today()
    head = struct.pack("<BBBBIHH20x", 3, d.year - 1900, d.month, d.day, 0, hlen, rlen)
    with open(path, "wb") as fh:
        fh.write(head + desc + b"\x0d" + b"\x1a")
    with Dbf(path) as t:
        t.append([t.encode(r) for r in rows])


def tag(name, equip="", item="", fmt="", comment=""):
    return {"NAME": name, "TYPE": "REAL", "COMMENT": comment, "EQUIP": equip, "ITEM": item, "FORMAT": fmt}


def make_install(root, projects, master_extra=()):
    """``projects`` = {name: {"include": [...], "tags": [row, ...], ...}}: the
    folders under root/inst/User, registered in its MASTER.DBF."""
    user = os.path.join(str(root), "inst", "User")
    os.makedirs(user, exist_ok=True)
    rows = []
    for name, spec in projects.items():
        d = os.path.join(user, name)
        os.makedirs(d, exist_ok=True)
        rows.append({"NAME": name, "PATH": d})
        if spec.get("include"):
            write_dbf(os.path.join(d, "include.dbf"), [("NAME", 64)], [{"NAME": n} for n in spec["include"]])
        if spec.get("tags"):
            write_dbf(os.path.join(d, "variable.dbf"), VAR_FIELDS, spec["tags"])
        if spec.get("equip"):
            write_dbf(
                os.path.join(d, "equip.dbf"),
                [("NAME", 64), ("TYPE", 32), ("COMMENT", 48)],
                [{"NAME": n, "TYPE": "Pump", "COMMENT": "from " + name} for n in spec["equip"]],
            )
        if spec.get("trends"):
            write_dbf(
                os.path.join(d, "trend.dbf"),
                [("NAME", 79), ("EXPR", 64), ("COMMENT", 48)],
                [{"NAME": n, "EXPR": n + "_PV"} for n in spec["trends"]],
            )
        if spec.get("clusters"):
            write_dbf(
                os.path.join(d, "cluster.dbf"),
                [("NAME", 16), ("COMMENT", 48)],
                [{"NAME": n} for n in spec["clusters"]],
            )
        if spec.get("pages"):
            write_dbf(
                os.path.join(d, "pages.dbf"),
                [("NAME", 64), ("TITLE", 64)],
                [{"NAME": n, "TITLE": n} for n in spec["pages"]],
            )
        if spec.get("ci"):
            with open(os.path.join(d, name + ".ci"), "w", encoding="ascii") as fh:
                fh.write(spec["ci"])
    rows.extend(master_extra)
    write_dbf(os.path.join(user, "MASTER.DBF"), [("NAME", 64), ("PATH", 200)], rows)
    return user


@pytest.fixture(autouse=True)
def _fresh_scope(monkeypatch):
    monkeypatch.delenv("CTG_LIB_PATH", raising=False)
    PR.clear_scope_caches()
    yield
    PR.clear_scope_caches()


#: Lib is included by Mid (included by Top) and by Other; Top also includes Side
LAYOUT = {
    "Lib": {"tags": [tag("LIB_TAG"), tag("DUP_TAG", fmt="#.#")], "pages": ["LibPage"]},
    "Mid": {"include": ["Lib"], "tags": [tag("MID_TAG")]},
    "Top": {
        "include": ["Mid", "Side"],
        "tags": [tag("TOP_TAG", equip="Pump1", item="Speed", fmt="###"), tag("DUP_TAG", fmt="###")],
        "equip": ["Pump1"],
        "trends": ["TOP_TREND"],
        "clusters": ["ClusterT"],
        "pages": ["TopPage", "LibPage"],
        "ci": "FUNCTION TopFn(INT a)\nEND\nPRIVATE FUNCTION Hidden()\nEND\n",
    },
    "Side": {"tags": [tag("SIDE_TAG")]},
    "Other": {"include": ["Lib"], "tags": [tag("OTHER_TAG")]},
}


@pytest.fixture
def install(tmp_path):
    return make_install(tmp_path, LAYOUT)


#: the parts of big_install's tag names: an area, a number, a signal
AREAS = ("wtp", "mix", "pump", "valve", "fxtank")
SIGNALS = ("pv", "sp", "run", "out", "int", "alm")
TYPES = ("REAL", "DIGITAL", "INT", "STRING", "LONG")


def big_rows(count, seed=0):
    """``count`` variable rows of several types: 'wtp_t2_pv', 'mix_t17_run' ... (the area
    and signal cycle, the number counts on), each on an equipment 'FxPlant.<area><n % 40>'
    with an item, the equipment list."""
    rows, equip = [], set()
    for k in range(count):
        area = AREAS[(k + seed) % len(AREAS)]
        eq = "FxPlant.%s%d" % (area, k % 40)
        equip.add(eq)
        rows.append(
            {
                "NAME": "%s_t%d_%s" % (area, k // len(AREAS), SIGNALS[k % len(SIGNALS)]),
                "TYPE": TYPES[(k // 3) % len(TYPES)],
                "COMMENT": "tag %d" % k,
                "EQUIP": eq,
                "ITEM": SIGNALS[k % len(SIGNALS)].upper(),
                "FORMAT": "",
            }
        )
    return rows, sorted(equip)


def big_install(root, names=("FxBigTags",), count=3000):
    """An install of projects ``names``, each with ``count`` generated tags (big_rows) and
    their equipment: the stand-in of a large example project's tag scope."""
    projects = {}
    for k, name in enumerate(names):
        rows, equip = big_rows(count, seed=k)
        projects[name] = {"tags": rows, "equip": equip}
    user = make_install(root, projects)
    return [os.path.join(user, n) for n in names]


def _lib(user):
    return os.path.join(user, "Lib")


# ------------------------------------------------------------------ scope order
def test_scope_order_own_include_includer_compiled(install):
    sc = TS.scope(_lib(install), root="Top")
    assert sc.root == "Top"
    assert [(p.name, p.relation) for p in sc.projects] == [
        ("Lib", TS.OWN),
        ("Mid", TS.INCLUDER),
        ("Top", TS.INCLUDER),
        ("Side", TS.COMPILED),
    ]
    # a project that includes others: own, then its includes in search order
    top = TS.scope(os.path.join(install, "Top"))
    assert [(p.name, p.relation) for p in top.projects] == [
        ("Top", TS.OWN),
        ("Mid", TS.INCLUDE),
        ("Lib", TS.INCLUDE),
        ("Side", TS.INCLUDE),
    ]
    assert top.root == "Top" and top.roots == ("Top",)
    # the far projects are the ones compiled with it
    assert [p.name for p in sc.near()] == ["Lib"]
    assert [p.name for p in sc.far()] == ["Mid", "Top", "Side"]


def test_root_choice_explicit_chosen_lastdatabase_then_name(install, monkeypatch):
    lib = _lib(install)
    from ctgedit.io import citect_config

    monkeypatch.setattr(citect_config, "ini_value", lambda *a, **k: "")
    assert TS.roots(lib) == ["Other", "Top"]
    assert TS.scope(lib).root == "Other"  # first by name
    monkeypatch.setattr(
        citect_config, "ini_value", lambda sec, key, *a, **k: "top" if key.lower() == "lastdatabase" else ""
    )
    assert TS.scope(lib).root == "Top"  # [CtEdit] LastDatabase
    TS.choose_root(lib, "Other")
    try:
        sc = TS.scope(lib)
        assert sc.root == "Other"  # the chosen one beats the ini
        assert [p.name for p in sc.projects] == ["Lib", "Other"]
        assert TS.scope(lib, root="Top").root == "Top"  # an explicit root beats both
    finally:
        TS.choose_root(lib, None)
    assert TS.scope(lib).root == "Top"


def test_the_remembered_root_applies_before_a_panel_is_shown(install):
    """The From: choice saved by a Tags panel (tags/scope/<project>) is the
    scope's root from the start of the next session (MainWindow restores them
    all before it reads a page), not only once a panel shows that project."""
    lib = _lib(install)
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.panels.tags_dock import restore_root_choices

    app_settings().setValue("tags/scope/lib", "Top")
    try:
        TS.clear()
        assert TS.chosen_root(lib) is None
        restore_root_choices()
        assert TS.chosen_root(lib) == "Top" and TS.scope(lib).root == "Top"
    finally:
        app_settings().remove("tags/scope/lib")


def test_scope_follows_an_include_dbf_change(install):
    lib = _lib(install)
    assert "Side" in TS.scope(lib, root="Top").names()
    time.sleep(0.05)
    write_dbf(os.path.join(install, "Top", "include.dbf"), [("NAME", 64)], [{"NAME": "Mid"}])
    assert "Side" not in TS.scope(lib, root="Top").names()


def test_ctg_lib_path_folder_joins_the_scope(install, tmp_path, monkeypatch):
    extra = tmp_path / "loose" / "Extra"
    extra.mkdir(parents=True)
    write_dbf(str(extra / "variable.dbf"), VAR_FIELDS, [tag("EXTRA_TAG")])
    monkeypatch.setenv("CTG_LIB_PATH", str(extra))
    sc = TS.scope(_lib(install), root="Top")
    assert sc.projects[-1].name == "Extra" and sc.projects[-1].relation == TS.OWN
    assert PR.tag_index(_lib(install)).get("EXTRA_TAG") is not None


# ------------------------------------------------------------------ tags
def test_tag_index_finds_an_includers_tag_with_its_project_and_equipment(install):
    lib = _lib(install)
    TS.choose_root(lib, "Top")
    idx = PR.tag_index(lib)
    e = idx.get("top_tag")
    assert e is not None and (e.name, e.project, e.equip, e.item) == ("TOP_TAG", "Top", "Pump1", "Speed")
    ref = idx.get("Pump1.Speed")
    assert ref is not None and ref.kind == "equipment" and ref.project == "Top"
    assert idx.get("OTHER_TAG") is None  # not compiled with Top
    assert idx.get("DUP_TAG").project == "Lib"  # the first definition wins
    names = idx.sorted_names(("variable",))
    assert names == sorted(names, key=str.lower) and len(set(n.lower() for n in names)) == len(names)
    assert {"LIB_TAG", "MID_TAG", "TOP_TAG", "SIDE_TAG"} <= set(names)
    # the project of each row, and a search capped at the limit
    hits = idx.search("tag", limit=2)
    assert len(hits) == 2
    assert {idx.project_of(c) for c in idx.order(("variable",))} == {"Lib", "Mid", "Top", "Side"}
    assert idx.counts(idx.order(("variable",)))["Top"] == 1
    # the near-only index (the Tags panel's first answer) has the page's own tags only
    assert PR.tag_index(lib, near_only=True).get("TOP_TAG") is None


def test_filter_on_fresh_tables_builds_nested_structures(install):
    """A table's structures build on each other (the Equip.Item search text
    reads the sorted Equip.Item rows): the first filter of a fresh index must
    not wait on itself."""
    import threading

    lib = _lib(install)
    TS.choose_root(lib, "Top")
    from ctgedit.format import dbfcols

    dbfcols.clear()
    idx = PR.tag_index(lib)
    order = idx.order(("variable",))
    out = []
    t = threading.Thread(target=lambda: out.append(idx.filter(order, "speed")), daemon=True)
    t.start()
    t.join(20)
    assert not t.is_alive(), "the filter deadlocked"
    assert [idx.name_of(c) for c in out[0]] == []  # names only: no tag holds 'speed'
    out.clear()
    t = threading.Thread(target=lambda: out.append(idx.search("speed")), daemon=True)
    t.start()
    t.join(20)
    assert not t.is_alive()
    assert [idx.name_of(c) for c in out[0]] == ["Pump1.Speed"]


def test_tag_index_is_cached_until_a_table_changes(install):
    lib = _lib(install)
    TS.choose_root(lib, "Top")
    idx = PR.tag_index(lib)
    assert PR.tag_index(lib) is idx and PR.cached_tag_index(lib) is idx
    time.sleep(0.05)
    write_dbf(os.path.join(install, "Top", "variable.dbf"), VAR_FIELDS, [tag("NEW_TAG")])
    again = PR.tag_index(lib)
    assert again is not idx and again.get("NEW_TAG").project == "Top"


def test_tag_format_map_is_identity_stable_and_shared_by_reader_and_editor(install, tmp_path):
    """A page measures with the formats of its project and the projects it includes, not
    of the projects that include it (CtDraw, LIVE: an include project's display
    of a tag only its includer defines took the default mask)."""
    lib = _lib(install)
    TS.choose_root(lib, "Top")
    fm = PR.tag_format_map(lib)
    assert fm is PR.tag_format_map(lib)
    assert fm("dup_tag") == "#.#" and fm("LIB_TAG") == ""
    assert fm("TOP_TAG") is None and fm("NOT_A_TAG") is None  # (Top includes Lib)
    top = PR.tag_format_map(os.path.join(install, "Top"))
    assert top("TOP_TAG") == "###" and top("DUP_TAG") == "###"  # its own first
    from ctgedit.io import reader

    page = os.path.join(lib, "Some.ctg")
    assert reader._tag_format_map(page) is fm
    from ctgedit.editing import EditableDocument

    doc = object.__new__(EditableDocument)
    doc.path = page
    assert doc.tag_format("DUP_TAG") == "#.#" and doc.tag_format("TOP_TAG") is None
    time.sleep(0.05)
    write_dbf(
        os.path.join(install, "Lib", "variable.dbf"), VAR_FIELDS, [tag("LIB_TAG"), tag("DUP_TAG", fmt="#.##")]
    )
    fm2 = PR.tag_format_map(lib)
    assert fm2 is not fm and fm2("DUP_TAG") == "#.##"
    assert reader._tag_format_map(page) is fm2


def test_format_lookup_of_a_dotted_name_reads_no_table(install):
    """A dotted name (an Equip.Item reference, Tag.Field) is never a variable
    tag: its lookup answers None without reading the scope's tables."""
    lib = _lib(install)
    TS.choose_root(lib, "Top")
    fm = PR.tag_format_map(lib)
    assert fm("Pump1.Speed") is None and fm("TOP_TAG.Field") is None
    assert all(t is None for t in fm._tables)
    assert fm("DUP_TAG") == "#.#"  # a plain name still reads them
    assert any(t is not None for t in fm._tables)


def test_tag_index_keeps_the_index_another_thread_stored(install, monkeypatch):
    """Two readers of the same scope (the Tags panel's worker, a completion)
    end with ONE index: the second keeps the first one stored, so the lists
    built on it stay current."""
    lib = _lib(install)
    TS.choose_root(lib, "Top")
    real = PR.TagIndex
    other = []

    class Racing(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            if not other:  # another thread finishes first
                other.append(None)
                other[0] = PR.tag_index(lib)

    monkeypatch.setattr(PR, "TagIndex", Racing)
    got = PR.tag_index(lib)
    assert other and got is other[0] and PR.cached_tag_index(lib) is got


def test_document_tag_names_do_not_wait_for_the_compiled_projects(install):
    """doc.tag_names() (the Properties panel's completion) lists the page's
    project and its includes until the whole scope is read, and the whole
    scope once it is: it never reads the compiled projects itself."""
    lib = _lib(install)
    TS.choose_root(lib, "Top")
    from ctgedit.editing import EditableDocument

    doc = object.__new__(EditableDocument)
    doc.path = os.path.join(lib, "Some.ctg")
    names = doc.tag_names()
    assert "LIB_TAG" in names and "TOP_TAG" not in names
    assert PR.cached_tag_index(lib) is None
    PR.tag_index(lib)
    assert "TOP_TAG" in doc.tag_names()


# ------------------------------------------------------------------ the other lists
def test_equipment_trends_clusters_pages_and_lists_use_the_scope(install):
    lib = _lib(install)
    TS.choose_root(lib, "Top")
    eq = PR.equipment(lib)
    assert [(r["NAME"], r["PROJECT"]) for r in eq] == [("Pump1", "Top")]
    assert eq[0].get("COMMENT") == "from Top" and eq[0].get("NOPE", "x") == "x"
    assert [(t.name, t.project) for t in PR.trend_tags(lib)] == [("TOP_TREND", "Top")]
    assert [(r["NAME"], r["PROJECT"]) for r in PR.clusters(lib)] == [("ClusterT", "Top")]
    assert PR.chain_page_names(lib) == ["LibPage", "TopPage"]  # own first, each once
    vals = PR.list_values(lib, "variable", "NAME")
    assert {"LIB_TAG", "TOP_TAG", "SIDE_TAG"} <= set(vals)
    assert PR.list_values(lib, "variable", "NAME", "TOP_*") == ["TAG"]
    if "variable tags" in PR.form_tables():
        assert "TOP_TAG" in PR.list_values(lib, "Variable Tags", "NAME")
    # the root that does not compile Top sees none of it
    TS.choose_root(lib, "Other")
    assert PR.equipment(lib) == () and PR.trend_tags(lib) == ()
    assert "TOP_TAG" not in PR.list_values(lib, "variable", "NAME")


def test_cicode_functions_come_from_the_scope(install):
    lib = _lib(install)
    TS.choose_root(lib, "Top")
    from ctgedit.io.cicode_catalog import FunctionCatalog, project_functions

    funcs = project_functions(lib)
    assert [(f.name, f.source) for f in funcs] == [("TopFn", "Top")]
    cat = FunctionCatalog(lib, builtins=())
    assert cat.get("topfn") is not None and cat.get("Hidden") is None
    assert cat.is_function("TopFn") is True


def test_insert_tag_lays_out_only_the_rows_it_shows(monkeypatch):
    """Insert Tag over a big scope: its list is a table view, which asks the
    model only about the rows on screen."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.panels import tags_dock as TD
    from ctgedit.ui.objprops.expression import ExpressionContext, TagPickerDialog

    entries = [PR.TagEntry("TAG_%05d" % n, "variable", "P%d" % (n % 3), "REAL") for n in range(30000)]
    ctx = ExpressionContext(None)
    ctx._index = PR.TagIndex(entries)
    calls = [0]
    real = TD.TagTableModel.flags

    def counted(self, index):
        calls[0] += 1
        return real(self, index)

    monkeypatch.setattr(TD.TagTableModel, "flags", counted)
    dlg = TagPickerDialog(None, ctx)
    try:
        dlg.show()
        app.processEvents()
        assert dlg.model.rowCount() == 30000
        dlg.search.setText("TAG_1")
        dlg.search.flush()
        dlg.search.setText("")
        dlg.search.flush()
        app.processEvents()
        assert dlg.model.rowCount() == 30000
        assert calls[0] < 5000, "%d flags() calls" % calls[0]
        assert dlg.chosen() == "TAG_00000"
    finally:
        dlg.reject()
        dlg.deleteLater()
        app.processEvents()


def test_completion_extras_are_sorted_once_and_matched_by_their_start():
    """Extra names a caller passes (a sheet's context) are sorted once per list,
    not copied and scanned on every key press; a list the size of a scope is
    matched by the start of its names only."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.objprops.expression import TagCompletionModel

    m = TagCompletionModel(None)
    m._index = PR.TagIndex([PR.TagEntry("PUMP_A", "variable", "P")])
    extras = ["pump_a", "Pump_B", "XPUMP"] + ["N%06d" % i for i in range(30000)]
    m.update("pump", extras)
    assert m.names() == ["Pump_B", "PUMP_A"]  # each name once; 'XPUMP' too many to scan
    prep = m._extra_prep
    m.update("pum", extras)
    assert m._extra_prep is prep and m.names() == ["Pump_B", "PUMP_A"]
    small = ["pump_a", "Pump_B", "XPUMP"]
    m.update("pump", small)
    assert m.names() == ["Pump_B", "PUMP_A", "XPUMP"]  # a short list: 'contains' matches too


# ------------------------------------------------------------------ the tag drop
def _register(fx_install, *names):
    """Rows for projects ``names`` (folders of the fx_install copy) in its MASTER.DBF."""
    master = os.path.join(fx_install.user, "MASTER.DBF")
    with Dbf(master) as t:
        width = dict((n.upper(), w) for n, _o, w in t.fields)
        rows = []
        for n in names:
            row = {k: "" for k in width}
            row.update({"NAME": n, "PATH": os.path.join(fx_install.user, n)})
            rows.append(t.encode(row))
        t.append(rows)


@heavy
def test_tag_drop_binds_equipment_from_an_includers_tag(fx_install, monkeypatch):
    """A tag drop binds equipment from an including project's tag."""
    lib = fx_install.new_project("FxExLib")
    plant = fx_install.new_project("FxPlantX", includes=("FxExLib",), tables=False)
    write_dbf(
        os.path.join(plant, "variable.dbf"), VAR_FIELDS, [tag("DROP_TAG", equip="DROP_EQUIP", item="Pos")]
    )
    _register(fx_install, "FxExLib", "FxPlantX")
    page = os.path.join(lib, "FxParts.ctg")
    shutil.copyfile(fx_install.page("FxParts"), page)
    PR.clear_scope_caches()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(page)
        doc = w.doc
        e = doc.tag_entry("DROP_TAG")
        assert e is not None and e.project == "FxPlantX" and e.equip == "DROP_EQUIP"
        (genie,) = doc.find_objects_by_name("FxAlarm1")
        assert doc.page.objects[genie].parent is None and doc.page.objects[genie].is_genie
        assert not doc.is_composite_genie(genie)
        assert list(doc.genie_all_params(genie)) == ["Equipment"]
        # the genie dialog completes from the scope's shared model
        shown = []
        w._show_modeless = lambda dlg, *_a, **_k: shown.append(dlg)
        w._edit_genie(genie, doc.page.objects[genie])
        from ctgedit.ui.objprops.expression import TagCompletionModel

        assert shown and isinstance(shown[0].params_form._names_model, TagCompletionModel)
        shown[0].reject()
        w._edit_genie = lambda *_a, **_k: None  # never block on a modal
        w._on_tag_dropped(genie, "DROP_TAG")
        assert w.doc.genie_all_params(genie)["Equipment"] == "DROP_EQUIP"
        assert "DROP_EQUIP" in w.statusBar().currentMessage()
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.hide()
        w.deleteLater()
        app.processEvents()


def test_tags_panel_filter_refines_the_last_matches_and_equals_a_full_filter(tmp_path):
    """Typing on filters the last matches; every step equals filtering all tags (big_install's
    generated tags and their equipment)."""
    from PySide6 import QtWidgets
    from ctgedit.io import project as PR
    from ctgedit.ui.panels.tags_dock import TagTableModel, DOCK_COLUMNS

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    (ex,) = big_install(tmp_path)
    idx = PR.tag_index(ex)
    assert len(idx) > 3000
    m = TagTableModel(DOCK_COLUMNS)
    m.set_source(idx, idx.order())
    full = TagTableModel(DOCK_COLUMNS)
    full.set_source(idx, idx.order())
    for text in ("w", "wt", "wtp", "wtp_t", "wtp_t2", "wtp", "p_t", ""):
        m.set_filter(text)
        full._last_filter = None
        full.set_filter(text)
        assert list(m._rows) == list(full._rows), text
        full._text = None  # (so the next set_filter recomputes)


def test_the_tags_panel_shows_tag_type_equipment_and_project_at_its_width():
    """The first widths keep Tag, Type, Equipment and Project in view at the panel's default
    width."""
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.panels import tags_dock as TD

    d = TD.TagsDock()
    try:
        d.resize(265, 600)
        d.show()
        app.processEvents()
        d._fit_columns()
        hdr = d._table.horizontalHeader()
        cols = [TD.DOCK_COLUMNS.index(c) for c in ("name", "type", "equip", "project")]
        total = sum(hdr.sectionSize(c) for c in cols)
        assert total <= d._table.viewport().width() + 12, (total, d._table.viewport().width())
        assert all(hdr.sectionSize(c) >= 50 for c in cols)
    finally:
        d.close()
        d.deleteLater()
        app.processEvents()


def test_filtering_few_codes_one_by_one_equals_the_table_pass(monkeypatch, tmp_path):
    """TagIndex.filter over few codes (typing on narrows the last matches) tests each code on
    its own: the same codes, in the same order, as the whole-table pass gives. Over
    big_install's generated tags of five types and their equipment."""
    (ex,) = big_install(tmp_path)
    idx = PR.tag_index(ex)
    codes = idx.order()
    assert len(codes) > 500
    queries = [
        "a",
        "e",
        "pump",
        "_",
        "1",
        "valve",
        "digital",
        "real",
        "FxBigTags",
        "zz",
        "mix",
        ".",
        "pv",
        "int",
    ]
    for kinds in (None, ("variable",), ("equipment",), ("variable", "equipment")):
        for project in (None, "FxBigTags"):
            for q in queries:
                monkeypatch.setattr(PR, "_PER_CODE_MIN", 0)
                monkeypatch.setattr(PR, "_PER_CODE_SHARE", 10**9)
                full = idx.filter(codes, q, project, kinds=kinds)
                monkeypatch.setattr(PR, "_PER_CODE_MIN", 10**9)
                fast = idx.filter(codes, q, project, kinds=kinds)
                assert list(fast) == list(full), (q, project, kinds)
                # and narrowing from the last result gives the same as filtering every code
                if len(q) > 1:
                    narrow = idx.filter(
                        idx.filter(codes, q[:-1], project, kinds=kinds), q, project, kinds=kinds
                    )
                    assert list(narrow) == list(full), (q, project, kinds)


def test_a_large_scopes_search_runs_on_a_worker_and_lands_the_last_key(monkeypatch, tmp_path):
    """A scope over TagsDock.ASYNC_ROWS tags is searched on a worker thread; the rows that
    land equal the direct filter, and a burst of keys ends on the last one (big_install:
    ASYNC_ROWS is set to 1, so its tags count as large)."""
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.panels import tags_dock as TD

    (ex,) = big_install(tmp_path)
    monkeypatch.setattr(TD.TagsDock, "ASYNC_ROWS", 1)
    d = TD.TagsDock()
    try:
        d.set_project_dir(ex)
        d.ensure_loaded()
        assert d.wait_filter()
        total = d._model.total()
        ref = TD.TagTableModel(TD.DOCK_COLUMNS)
        ref.set_source(d._model.index_, d._model._base)
        for text in ("w", "wt", "wtp", "wtp_t2"):
            d._filter.setText(text)
            d._filter.flush()  # (a burst: no wait between the keys)
        assert d.wait_filter()
        ref.set_filter("wtp_t2")
        assert d._model.params()[0] == "wtp_t2"
        assert list(d._model._rows) == list(ref._rows)
        assert 0 < d._model.rowCount() < total
        d._filter.setText("")
        d._filter.flush()
        assert d.wait_filter() and d._model.rowCount() == total
    finally:
        d.close()
        d.deleteLater()
        app.processEvents()


def test_a_page_measures_with_its_own_and_included_projects_formats_only(tmp_path):
    """A page measures with its own and included projects' formats only."""
    user = make_install(
        tmp_path,
        {
            "FxIncLib": {},
            "FxIncTop": {"include": ["FxIncLib"], "tags": [tag("fx_loop_opm", fmt="#")]},
            "FxIncOther": {"include": ["FxIncLib"]},
        },
    )
    lc, ex = (os.path.join(user, n) for n in ("FxIncLib", "FxIncTop"))
    TS.choose_root(lc, "FxIncTop")
    try:
        assert (PR.tag_format_map(ex) or {}).get("fx_loop_opm") == "#"
        assert (PR.tag_format_map(lc) or {}).get("fx_loop_opm") is None
        # the scope's lists still have it (the Tags panel, the pickers)
        assert PR.tag_index(lc).get("fx_loop_opm") is not None
        from ctgedit.editing import EditableDocument

        page = os.path.join(lc, "FxIncPage.ctg")
        shutil.copyfile(os.path.join(fxinstall.FX_INSTALL, "User", "FxMain", "FxThumb.ctg"), page)
        doc = EditableDocument.open(page)
        assert doc.tag_format("fx_loop_opm") is None
    finally:
        TS.choose_root(lc, None)


def test_the_index_build_lets_the_gui_thread_have_the_gil(monkeypatch, tmp_path):
    """dbfcols.breathe: nothing on the GUI thread; on a worker a time.sleep(0), or a nap of
    NAP_S in the tag index build (so the GUI thread gets the GIL); the build on a worker naps,
    and its index is the one the GUI thread builds."""
    (ex,) = big_install(tmp_path)
    import threading
    from ctgedit.format import dbfcols

    sleeps = []
    real_sleep = time.sleep
    monkeypatch.setattr(dbfcols.time, "sleep", lambda s: sleeps.append(s) or real_sleep(s))
    dbfcols.breathe()
    dbfcols.breathe(True)
    assert sleeps == []  # the GUI thread keeps the GIL
    t = threading.Thread(target=lambda: (dbfcols.breathe(), dbfcols.breathe(True)))
    t.start()
    t.join()
    assert sleeps == [0, dbfcols.NAP_S]
    naps = []
    real = dbfcols.breathe
    monkeypatch.setattr(dbfcols, "breathe", lambda nap=False: naps.append(nap) or real(nap))
    PR.clear_scope_caches()
    dbfcols.clear()
    got = []
    t = threading.Thread(target=lambda: got.append(list(PR.tag_index(ex).order())))
    t.start()
    t.join()
    assert got and any(naps)
    PR.clear_scope_caches()
    dbfcols.clear()
    assert list(PR.tag_index(ex).order()) == got[0]


def _tags_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_a_large_scopes_column_sort_runs_on_the_worker(monkeypatch, tmp_path):
    """A column sort over TagsDock.ASYNC_ROWS tags runs on the search worker: the header click
    returns at once, the rows that land are the GUI thread's own sort, and a search meanwhile
    keeps the new sort (big_install's tags of five types, ASYNC_ROWS 1)."""
    import threading
    from PySide6 import QtCore

    app = _tags_app()
    from ctgedit.ui.panels import tags_dock as TD

    (ex,) = big_install(tmp_path)
    monkeypatch.setattr(TD.TagsDock, "ASYNC_ROWS", 1)
    d = TD.TagsDock()
    try:
        d.set_project_dir(ex)
        d.ensure_loaded()
        assert d.wait_filter()
        m = d._model
        ref = TD.TagTableModel(TD.DOCK_COLUMNS)
        ref.set_source(m.index_, m._base)
        before = list(m._rows)
        on_gui = []
        real = TD.TagTableModel._rows_for
        main = threading.main_thread()

        def rows_for(self, *a, **k):
            on_gui.append(threading.current_thread() is main)
            return real(self, *a, **k)

        monkeypatch.setattr(TD.TagTableModel, "_rows_for", rows_for)
        d._table.sortByColumn(1, QtCore.Qt.DescendingOrder)  # Type, as a header click
        assert list(m._rows) == before  # (not on this thread)
        assert d.wait_filter()
        assert on_gui and not any(on_gui)  # only the worker sorted
        monkeypatch.setattr(TD.TagTableModel, "_rows_for", real)
        ref.sort(1, QtCore.Qt.DescendingOrder)
        assert list(m._rows) == list(ref._rows) != before
        # a search keeps the sort; then back to the names
        d._filter.setText("wtp")
        d._filter.flush()
        assert d.wait_filter()
        ref.set_filter("wtp")
        assert list(m._rows) == list(ref._rows)
        d._table.sortByColumn(0, QtCore.Qt.AscendingOrder)
        assert d.wait_filter()
        ref.sort(0, QtCore.Qt.AscendingOrder)
        assert list(m._rows) == list(ref._rows)
    finally:
        d.close()
        d.deleteLater()
        app.processEvents()


def test_a_tab_switch_keeps_the_project_counts_and_column_widths(monkeypatch, tmp_path):
    """The Tags panel shows a list it showed before (a tab switch between two pages)
    without counting its projects or measuring its Type column again. Two big_install
    projects of 3,000 tags."""
    app = _tags_app()
    from ctgedit.ui.panels import tags_dock as TD

    a, b = big_install(tmp_path, ("FxBigA", "FxBigB"))
    counts, fits = [], []
    real_counts = PR.TagIndex.counts
    monkeypatch.setattr(
        PR.TagIndex, "counts", lambda self, codes: counts.append(1) or real_counts(self, codes)
    )
    d = TD.TagsDock()
    real_fit = d._table.resizeColumnToContents
    d._table.resizeColumnToContents = lambda c: fits.append(c) or real_fit(c)
    try:
        widths = {}
        for k in range(6):
            p = a if k % 2 == 0 else b
            d.set_project_dir(p)
            d.ensure_loaded()
            hdr = d._table.horizontalHeader()
            w = [hdr.sectionSize(c) for c in range(len(TD.DOCK_COLUMNS))]
            assert widths.setdefault(p, w) == w
            assert d._project.count() > 0
        assert len(counts) == 2 and len(fits) == 2
    finally:
        d.close()
        d.deleteLater()
        app.processEvents()
