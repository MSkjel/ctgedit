"""The object sheet builds each page on first show."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app

#: the page the new objects go on (it has a root text FxText, not from a template)
MIXER = "FxShapes"
#: genie instances with drawable members
GENIES = "FxGenies"
#: the pages whose objects the inert check runs over
INERT_PAGES = ("FxShapes", "FxGenies", "FxAnim", "FxTplLinked", "FxLegacy")

pytestmark = pytest.mark.usefixtures("collect_between_tests")


@pytest.fixture
def page_of(fx_page, monkeypatch):
    """``page_of(name)``: the path of FxMain's page ``name`` of the fake install copy, with
    CTG_LIB_PATH on its project (the include chain finds its libraries)."""

    def get(name):
        path = fx_page(name)
        monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(path))
        return path

    return get


def _doc(tmp_path, src, name=None):
    from ctgedit.editing import EditableDocument

    d = tmp_path / (name or "p")
    d.mkdir(exist_ok=True)
    dst = d / os.path.basename(src)
    shutil.copyfile(src, dst)
    return EditableDocument.open(str(dst))


def _sheet(doc, index, **kw):
    from ctgedit.ui.objprops.sheet import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index, **kw)


def _built(dlg):
    return {pid: dlg.is_page_built(pid) for pid in dlg.page_ids()}


def _module_of(dlg, pid):
    return next(p.module for p, _w, _k in dlg._pages if p.id == pid)


def test_the_open_builds_the_page_shown_and_its_module(page_of, tmp_path):
    doc = _doc(tmp_path, page_of(MIXER))
    r = doc.create_object("rectangle", (10, 10, 100, 80), None, colour=0x00808080)
    dlg = _sheet(doc, r)
    built = _built(dlg)
    first = dlg.page_ids()[0]
    assert built[first]
    shown_module = _module_of(dlg, first)
    assert all(built[p] == (_module_of(dlg, p) == shown_module) for p in built), built
    assert not built["move_h"] and not built["fill_color"]
    # a page shown is built with its module's pages (the slider shares the movement's
    # offsets); another module stays as it was
    assert dlg.show_page("move_h")
    built = _built(dlg)
    assert built["move_h"] and built["slider_h"] and built["scale_v"]
    assert not built["fill_color"]
    # the tab switched to by hand too
    k = next(i for i in range(dlg._tabs.count()) if dlg._tabs.tabText(i) == "Fill")
    dlg._tabs.setCurrentIndex(k)
    assert dlg.is_page_built("fill_color")
    # code outside the sheet that reads its fields gets every page
    assert "DISABLED" in dlg._widgets
    assert all(_built(dlg).values())


def test_pages_that_share_a_field_are_built_together(page_of, tmp_path):
    """The Display Value page owns Appearance > General's caption field: a text's sheet
    builds both at once. An editor a page builder sets on the sheet ('fill_color_editor')
    read from outside gets every page built."""
    doc = _doc(tmp_path, page_of(MIXER))
    dlg = _sheet(doc, _first_text(doc))
    assert dlg.is_page_built("display_value") and dlg._caption_owner is not None
    assert not dlg.is_page_built("fill_color")
    assert dlg.fill_color_editor is not None and all(_built(dlg).values())
    with pytest.raises(AttributeError):
        dlg.no_such_editor


def test_a_page_built_later_shows_the_page_as_it_is_now(page_of, tmp_path):
    """The record changed underneath before the page was first shown: the page shows the
    record as it is then (as a built page reloads), with nothing pending."""
    doc = _doc(tmp_path, page_of(MIXER))
    r = doc.create_object("rectangle", (10, 10, 100, 80), None)
    dlg = _sheet(doc, r)
    assert not dlg.is_page_built("disable")
    assert doc.ensure_property(r, "DISABLED", "Tag_1")
    dlg.reload()
    dlg.show_page("disable")
    assert dlg.field_widget("DISABLED").text() == "Tag_1"
    assert not dlg.has_pending_edits()
    assert dlg.tab_marked("Access", "Disable")


def _objects(doc, limit=40):
    """One object per (record type, kind, dynamic) class of the page, roots and members."""
    seen, out = set(), []
    for i, po in enumerate(doc.parsed.page.objects):
        o = doc.page.objects[i] if i < len(doc.page.objects) else None
        if o is None or o.raw.get("_non_drawable"):
            continue
        key = (po.type_code, o.kind.value, bool(po.subtype_size & 0x10), o.parent is None)
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out[:limit]


@pytest.mark.parametrize("src", INERT_PAGES)
def test_untouched_pages_of_inert_modules_change_nothing(page_of, tmp_path, src):
    """The contract a page module states with INERT_UNTOUCHED: built and left alone, its
    fields give no edit, its editors give no key edit and no problem, and their Apply
    writes nothing. So a page not built gives what the record holds, as it would have."""
    from ctgedit.ui.objprops import modules as M

    doc = _doc(tmp_path, page_of(src))
    data = doc.serialize()
    checked = set()
    for i in _objects(doc):
        dlg = _sheet(doc, i)
        dlg.build_all_pages()
        assert not dlg._unbuilt
        inert = {
            mod
            for mod in {p.module for p, _w, _k in dlg._pages}
            if getattr(M.load(mod), "INERT_UNTOUCHED", False)
        }
        # the fields: nothing typed, no edit (each key's page is in an inert module or not;
        # the fields of every page compare with what they loaded)
        assert [
            k
            for k, _v in dlg._collect_edits()
            if k in dlg._fields and not dlg._fields[k][0][0].kind == "slmode"
        ] == []
        undo = len(doc._undo)
        for ed in list(dlg.built_editors()):
            mod = type(ed).__module__.rsplit(".", 1)[-1]
            if mod not in inert:
                continue
            checked.add(mod)
            assert not (getattr(ed, "key_edits", None) or (lambda: []))(), (i, mod)
            assert (getattr(ed, "problem", None) or (lambda: None))() is None, (i, mod)
            assert (getattr(ed, "ok_problem", None) or (lambda: None))() is None, (i, mod)
            if dlg.read_only_reason:
                continue
            with doc.batch("probe"):
                assert not ed.apply(), (i, mod)
        assert len(doc._undo) == undo and doc.serialize() == data, i
        _free(dlg)
        dlg = None
    assert checked  # (the pages have editor pages)


def _free(dlg) -> None:
    """Close and delete a sheet now, before the next is built: left to deleteLater with no
    event loop running, the sheets wait for the garbage collector, which can free one in the
    middle of building the next (an access violation)."""
    import gc

    from PySide6 import QtCore, QtWidgets

    dlg.close()
    dlg.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    gc.collect()


def _first_text(doc):
    from ctgedit.model.document import ObjectKind

    return next(
        i
        for i, o in enumerate(doc.page.objects)
        if o.kind is ObjectKind.TEXT
        and o.parent is None
        and not o.raw.get("_non_drawable")
        and not o.raw.get("_from_template")
    )


def _scenarios():
    """(name, object maker, steps): a step is (page id or None, fn(dlg)); the lazy sheet
    shows each page just before its step, the eager one has every page built at once."""

    def rect(doc):
        return doc.create_object("rectangle", (10, 10, 100, 80), None, colour=0x00808080)

    def w(dlg, key, n=0):
        return dlg.field_entries(key)[n][1]

    def clear(dlg, key):
        from ctgedit.ui.objprops import modules as M

        sub = next(s for s in M.all_subs() if s.primary == key)
        dlg._clear(sub)

    return [
        (
            "visibility and movement",
            rect,
            [
                ("visibility", lambda d: w(d, "VISIBLE").setText("Tag_1 > 2")),
                ("move_h", lambda d: w(d, "MVX").setText("Level_1")),
            ],
        ),
        (
            "slider with the movement's offsets",
            rect,
            [
                ("slider_h", lambda d: w(d, "MVXSL").setText("Level_2")),
                ("slider_h", lambda d: w(d, "MVXMAXPT", 0).setValue(320)),
            ],
        ),
        (
            "disable writes its style",
            rect,
            [
                ("disable", lambda d: w(d, "DISABLED").setText("Tag_2")),
            ],
        ),
        (
            "rotation centre",
            rect,
            [
                ("move_rot", lambda d: w(d, "ROT").setText("Angle_1")),
                ("move_rot", lambda d: d.motion_editor.axis_picker("rot").set_state((True, 5, 0))),
            ],
        ),
        (
            "clear a property",
            lambda doc: _with(
                doc, rect(doc), {"VISIBLE": "Tag_1", "MVX": "Level_1", "MVXMINPT": "0", "MVXMAXPT": "500"}
            ),
            [
                ("move_h", lambda d: clear(d, "MVX")),
                ("visibility", lambda d: w(d, "VISIBLE").setText("Tag_3")),
            ],
        ),
        (
            "text caption and access",
            _first_text,
            [
                ("access", lambda d: w(d, "COMMENT").setText("A tip")),
            ],
        ),
        # (the Display Value page owns the caption of Appearance > General: built with it)
        (
            "a caption typed on the general page",
            _first_text,
            [
                (None, lambda d: d._ed_caption.setText("Typed caption")),
            ],
        ),
    ]


def _with(doc, i, props):
    for k, v in props.items():
        assert doc.ensure_property(i, k, v)
    return i


@pytest.mark.parametrize("name,make,steps", _scenarios(), ids=[s[0] for s in _scenarios()])
def test_a_sheet_shown_page_by_page_writes_what_a_built_one_writes(page_of, tmp_path, name, make, steps):
    src = page_of(MIXER)
    out = []
    for lazy in (True, False):
        doc = _doc(tmp_path, src, "lazy" if lazy else "eager")
        i = make(doc)
        dlg = _sheet(doc, i)
        if not lazy:
            dlg.build_all_pages()
        for pid, fn in steps:
            if lazy and pid is not None:
                assert dlg.show_page(pid)
            fn(dlg)
        if lazy:
            # (pages were left unbuilt; Clear Property builds them all: which keys another
            # property keeps is known only then)
            assert bool(dlg._unbuilt) == ("clear" not in name)
        n = len(doc._undo)
        assert dlg.apply() and len(doc._undo) == n + 1
        out.append((list(doc.object_properties(i).items()), doc.serialize()))
        _free(dlg)
        dlg = None
    assert out[0][0] == out[1][0]
    assert out[0][1] == out[1][1]


def test_ok_and_apply_build_the_input_tab(page_of, tmp_path):
    """The Input tab's own checks look at what the object stores (a keyboard command
    without a key sequence, commands an earlier version wrote): it is built for an
    Apply / OK even when it was never shown."""
    from ctgedit.ui.objprops import modules as M

    assert not getattr(M.load("input"), "INERT_UNTOUCHED", False)
    assert not getattr(M.load("vertices"), "INERT_UNTOUCHED", False)
    doc = _doc(tmp_path, page_of(MIXER))
    b = doc.create_object("button", (10, 10, 90, 40), None, text="Go")
    dlg = _sheet(doc, b)
    assert not dlg.is_page_built("touch")
    dlg.apply()
    assert dlg.is_page_built("touch") and dlg._input_tab is not None
    assert not dlg.is_page_built("fill_color")  # (an inert page stays as it is)


def test_a_read_only_sheet_shows_later_pages_read_only(page_of, tmp_path):
    doc = _doc(tmp_path, page_of(GENIES))
    m = next(
        i
        for i, o in enumerate(doc.page.objects)
        if o.parent is not None
        and doc.page.objects[doc.page.root_of(i)].is_genie
        and o.kind.value in ("rectangle", "ellipse", "polygon")
    )
    dlg = _sheet(doc, m, read_only=True)
    assert dlg.is_read_only()
    later = next(pid for pid in dlg.page_ids() if not dlg.is_page_built(pid))
    dlg.show_page(later)
    for entries in dlg._fields.values():
        for _f, w in entries:
            if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox)):
                assert w.isReadOnly()
            else:
                assert not w.isEnabled()
