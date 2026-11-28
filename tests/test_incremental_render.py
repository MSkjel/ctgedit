"""Incremental re-derive after an edit."""

import os
import random
import shutil
import tempfile
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.editing import EditableDocument  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format.page import parse_ctg_bytes  # noqa: E402
from ctgedit.io import reader  # noqa: E402
from ctgedit.model.document import ObjectKind as K  # noqa: E402
from qtkit import app as _app
from timing import check_speed

#: the pages: (page, project) of the fake install
LARGE = ("FxLarge", "FxPerf")  # the big page with a template, genies and groups
GENIES = ("FxGenies", "FxMain")
SA_PAGE = ("FxSaContent", "FxSa")  # a page on a no-title-bar HD1080 template
PARTS = ("FxParts", "FxMain")  # one instance of each fxparts genie
HUGE = ("FxHuge", "FxPerf")  # 5,000 records
SHAPES = ("FxShapes", "FxMain")
TPL_PAGE = ("FxTplLinked", "FxMain")

_FX = []


@pytest.fixture(autouse=True)
def _fx(fx_install):
    """Every test here reads the fake install's pages (:func:`_path`)."""
    _FX[:] = [fx_install]
    yield
    _FX.clear()


def _path(key) -> str:
    """The .ctg of a (page, project) of the fake install copy."""
    page, project = key
    return _FX[0].page(page, project)


def _open_copy(src, tmp_path, monkeypatch):
    """A copy of ``src`` (a (page, project) key) in tmp_path that resolves its symbols on
    its project's include chain."""
    src = _path(src)
    dst = tmp_path / os.path.basename(src)
    shutil.copy2(src, dst)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(src))
    return EditableDocument.open(str(dst))


def _first(doc, kinds, styled=True, root=True):
    for i, o in enumerate(doc.page.objects):
        if (
            (not root or o.parent is None)
            and o.kind in kinds
            and (not styled or doc.objects[i].subtype_size & 2)
            and not o.raw.get("_from_template")
        ):
            return i
    return None


def _full(doc):
    return reader.derive_page(parse_ctg_bytes(doc.serialize()), doc.path, records_fresh=True)[0]


# --------------------------------------------------------------------------- derive
def test_body_edit_rederives_only_its_unit(tmp_path, monkeypatch):
    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    rect = _first(doc, (K.RECTANGLE,))
    kept = [o for i, o in enumerate(doc.page.objects) if doc.page.root_of(i) != rect]
    serial = doc.derive_serial
    assert doc.set_colour(rect, 0x123456)
    der = doc._derivation
    assert not der.full and der.same_layout
    assert der.changed_roots == [rect]
    # every other unit keeps its derived objects; the edited one is new
    assert all(
        a is b
        for a, b in zip(kept, (o for i, o in enumerate(doc.page.objects) if doc.page.root_of(i) != rect))
    )
    assert doc.model_changes_since(serial) == {rect}
    assert reader.model_difference(doc.page, _full(doc)) == ""


def test_structural_edit_reuses_shifted_units(tmp_path, monkeypatch):
    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    roots = doc.page.roots()
    victim = roots[len(roots) // 2]
    after = roots[roots.index(victim) + 1]
    obj_after = doc.page.objects[after]
    doc.delete_objects([victim])
    der = doc._derivation
    assert not der.full and not der.same_layout and der.tail == victim
    # the unit after the deleted one moved down and kept its derived object
    assert any(o is obj_after for o in doc.page.objects)
    # a view rebuilds every root from the deleted one's place on
    assert doc.model_changes_since(doc.derive_serial - 1) >= {r for r in doc.page.roots() if r >= victim}
    assert reader.model_difference(doc.page, _full(doc)) == ""
    doc.undo()
    assert reader.model_difference(doc.page, _full(doc)) == ""


def test_in_place_move_is_rederived_after_undo(tmp_path, monkeypatch):
    """A move shifts the model in place (no derive); moving back and editing
    elsewhere must not keep the shifted objects of the moved unit."""
    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    g = next(
        i
        for i, o in enumerate(doc.page.objects)
        if o.parent is None and o.members and not o.raw.get("_from_template")
    )
    serial = doc.derive_serial
    doc.move_objects([g], 7, 3)
    assert doc.model_changes_since(serial) == {g}  # the scene rebuilds it
    doc.undo()
    doc.set_colour(_first(doc, (K.RECTANGLE,)), 0x00FF00)
    assert g in doc._derivation.changed_roots
    assert reader.model_difference(doc.page, _full(doc)) == ""


def test_derive_check_catches_a_stale_unit(tmp_path, monkeypatch):
    """The debug check is real: an undetected record change is reported."""
    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    rect = _first(doc, (K.RECTANGLE,))
    real = reader.record_sig

    def blind(po):  # hides every rectangle's body change
        s = real(po)
        return s[:5] + (b"",) if po.type_code == 0x0F else s

    monkeypatch.setattr(reader, "record_sig", blind)
    doc._derivation.sigs = [blind(po) for po in doc._derivation.records]
    doc.derive_check = True
    with pytest.raises(AssertionError, match="differs from a full one"):
        doc.set_colour(rect, 0x123456)


def test_label_mode_change_derives_everything(tmp_path, monkeypatch):
    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    reader.set_label_mode("literal")
    doc._rerender_page()
    assert doc._derivation.full
    assert reader.model_difference(doc.page, _full(doc)) == ""


# --------------------------------------------------------------------------- session
def _session(doc, rng, n):
    """``n`` random edits of every kind: body edits (colour, width, fill, text,
    bag keys, genie members), structural (create, delete, duplicate, group,
    ungroup, z-order), in-place (move, lock), resize / rotate, batches, undo,
    redo."""

    def any_obj(kinds=None, styled=False):
        idx = [
            i
            for i, o in enumerate(doc.page.objects)
            if not o.raw.get("_non_drawable")
            and (kinds is None or o.kind in kinds)
            and (not styled or doc.objects[i].subtype_size & 2)
        ]
        return rng.choice(idx) if idx else None

    def any_root():
        r = doc.page.roots()
        return rng.choice(r) if r else None

    def colour():
        i = any_obj((K.RECTANGLE, K.ELLIPSE, K.POLYGON, K.LINE, K.TEXT, K.PIPE))
        if i is not None:
            doc.set_colour(i, rng.randrange(0x1000000))

    def width():
        i = any_obj((K.RECTANGLE, K.ELLIPSE, K.POLYGON, K.LINE))
        if i is not None:
            doc.set_line_width(i, rng.randint(1, 4))

    def filled():
        i = any_obj((K.RECTANGLE, K.ELLIPSE))
        if i is not None:
            doc.set_filled(i, rng.random() < 0.5)

    def text():
        i = any_obj((K.TEXT,))
        if i is not None:
            doc.set_text(i, "T%d" % rng.randint(0, 999))

    def create():
        x, y = rng.randint(0, 800), rng.randint(0, 600)
        kind = rng.choice(["rectangle", "ellipse", "text", "line", "rectangle_outline"])
        if kind == "line":
            doc.create_object(kind, nodes=[(x, y), (x + 50, y + 20)], colour=0xFF0000)
        elif kind == "text":
            doc.create_object(kind, rect=(x, y, x + 60, y + 16), text="New", colour=0)
        else:
            doc.create_object(kind, rect=(x, y, x + 40, y + 30), colour=0x00FF00)

    def delete():
        r = any_root()
        if r is not None:
            doc.delete_objects([r])

    def duplicate():
        r = any_root()
        if r is not None:
            doc.duplicate_objects([r])

    def move():
        r = any_root()
        if r is not None:
            doc.move_objects([r], rng.randint(-20, 20), rng.randint(-20, 20))

    def group():
        rs = doc.page.roots()
        if len(rs) >= 2:
            doc.group_objects(rng.sample(rs, 2))

    def ungroup():
        gs = [r for r in doc.page.roots() if doc.page.objects[r].members and doc.objects[r].type_code == 0x08]
        if gs:
            doc.ungroup_object(rng.choice(gs))

    def reorder():
        r = any_root()
        if r is not None:
            doc.reorder([r], rng.choice(["front", "back", "forward", "backward"]))

    def lock():
        r = any_root()
        if r is not None:
            doc.set_locked([r], rng.random() < 0.5)

    def rotate():
        r = any_root()
        if r is not None:
            doc.rotate_objects([r], rng.random() < 0.5)

    def resize():
        i = any_obj((K.RECTANGLE, K.ELLIPSE, K.GROUP, K.GENIE))
        if i is not None:
            g = doc.page.objects[i].geometry
            doc.set_object_rect(
                i,
                (
                    g.x,
                    g.y,
                    g.x + max(2, g.extent_x + rng.randint(-5, 15)),
                    g.y + max(2, g.extent_y + rng.randint(-5, 15)),
                ),
            )

    def prop():
        i = any_obj()
        if i is not None:
            doc.ensure_property(i, "COMMENT", "c%d" % rng.randint(0, 99))

    def member_colour():
        gs = [r for r in doc.page.roots() if doc.page.objects[r].members]
        if gs:
            g = rng.choice(gs)
            cand = [
                j
                for j in doc.page.subtree(g)
                if doc.objects[j].subtype_size & 2
                and doc.page.objects[j].kind in (K.RECTANGLE, K.TEXT, K.ELLIPSE)
            ]
            if cand:
                doc.set_colour(rng.choice(cand), rng.randrange(0x1000000))

    def undo():
        for _ in range(rng.randint(1, 3)):
            doc.undo()

    def redo():
        for _ in range(rng.randint(1, 2)):
            doc.redo()

    def batch():
        idx = [
            i
            for i, o in enumerate(doc.page.objects)
            if o.kind in (K.RECTANGLE, K.TEXT) and doc.objects[i].subtype_size & 2
        ]
        with doc.batch("recolour"):
            for i in rng.sample(idx, min(4, len(idx))):
                doc.set_colour(i, rng.randrange(0x1000000))

    ops = [
        (colour, 6),
        (width, 3),
        (filled, 2),
        (text, 4),
        (create, 4),
        (delete, 3),
        (duplicate, 2),
        (move, 4),
        (group, 2),
        (ungroup, 1),
        (reorder, 3),
        (lock, 1),
        (rotate, 1),
        (resize, 2),
        (prop, 2),
        (member_colour, 3),
        (undo, 5),
        (redo, 3),
        (batch, 1),
    ]
    bag = [f for f, w in ops for _ in range(w)]
    for _ in range(n):
        op = rng.choice(bag)
        try:
            op()
        except AssertionError:
            raise
        except Exception:  # noqa: BLE001 -- an edit that does not apply to that object
            pass
    doc._rerender_page()  # the in-place edits since the last derive


@pytest.mark.parametrize(
    "src,seed,mode", [(LARGE, 1, "literal"), (SA_PAGE, 2, "literal"), (PARTS, 3, "native")]
)
def test_scripted_session_matches_full_derive(src, seed, mode, tmp_path, monkeypatch):
    """200 edits with the debug check on: after every derive the incremental
    model equals a full derive of the page as it would be saved."""
    reader.set_label_mode(mode)
    doc = _open_copy(src, tmp_path, monkeypatch)
    doc.derive_check = True
    _session(doc, random.Random(seed), 200)
    assert doc.derive_serial > 100
    assert sum(1 for s, ch in doc._derive_log if ch is not None) > 0


# --------------------------------------------------------------------------- bytes
def _old_rerender(self) -> None:
    """The file-based re-render, kept to prove the saved bytes are unchanged:
    serialise -> a %TEMP% file -> read_page, the document's directory on
    CTG_LIB_PATH."""
    if getattr(self, "_batching", 0):
        self._rerender_pending = True
        return
    data = self.serialize()
    tmp = os.path.join(self._old_tmp, "_old_render.ctg")
    with open(tmp, "wb") as f:
        f.write(data)
    prev = os.environ.get("CTG_LIB_PATH")
    lib_dir = os.path.dirname(os.path.abspath(self.path))
    os.environ["CTG_LIB_PATH"] = (lib_dir + os.pathsep + prev) if prev else lib_dir
    try:
        self.page = reader.read_page(tmp)
    finally:
        if prev is None:
            os.environ.pop("CTG_LIB_PATH", None)
        else:
            os.environ["CTG_LIB_PATH"] = prev
    self.page.name = os.path.splitext(os.path.basename(self.path))[0]
    self.page.source_path = self.path


def _edit_set(doc) -> list:
    """The edits other tests make, for comparing incremental and full derives."""
    log = []
    st = {}

    def run(fn):
        try:
            r = fn()
            log.append(("ok", r if isinstance(r, (int, bool, list, type(None))) else "?"))
            return r
        except AssertionError:
            raise
        except Exception as e:  # noqa: BLE001
            log.append((type(e).__name__, str(e)[:80]))
            return None

    def genie():
        return next(
            (
                i
                for i, o in enumerate(doc.page.objects)
                if o.parent is None and o.members and not o.raw.get("_from_template")
            ),
            None,
        )

    def resize_genie():
        g = genie()
        l, t, r, b = G.norm_rect(G.parse_geom_header(doc.objects[g].body_raw).rect1)
        doc.set_object_rect(g, (l, t, r + 10, b + 6))

    def member_colour():
        g = genie()
        m = next(j for j in doc.subtree(g)[1:] if doc.objects[j].subtype_size & 2)
        return doc.set_colour(m, 0xABCDEF)

    def batch():
        with doc.batch("recolour"):
            for i in range(len(doc.objects) - 3, len(doc.objects)):
                doc.set_colour(i, 0x010203)

    def mid_root():
        roots = doc.page.roots()
        return roots[len(roots) // 2]

    run(lambda: doc.move_object(0, 7, 9))
    run(doc.undo)
    run(doc.redo)
    for kind, kw in (
        ("rectangle", dict(rect=(10, 10, 60, 60), colour=0x336699)),
        ("rectangle_outline", dict(rect=(340, 50, 440, 120), colour=0x4080FF)),
        ("ellipse", dict(rect=(50, 150, 150, 230), colour=0xFFA000)),
        ("ellipse_outline", dict(rect=(170, 150, 270, 230))),
        ("polygon", dict(nodes=[(300, 150), (400, 160), (380, 230)], colour=0xA040FF)),
        ("pipe", dict(nodes=[(50, 260), (200, 260), (200, 320)], colour=0x808080)),
        ("line", dict(nodes=[(200, 10), (10, 150)], colour=0xFF0000)),
        ("text", dict(rect=(220, 260, 380, 280), text="Hello", colour=0xFFFFFF)),
        ("button", dict(rect=(400, 260, 500, 300), text="Press")),
        ("number", dict(rect=(500, 400, 560, 420), text="Tag1")),
    ):
        st[kind] = run(lambda: doc.create_object(kind, **kw))
    run(doc.undo)
    run(doc.redo)
    run(lambda: doc.reorder([len(doc.objects) - 1], "back"))
    run(lambda: doc.reorder([0], "front"))
    run(lambda: doc.paste_payload(doc.copy_payload([0, 1]), offset=5))
    a = run(lambda: doc.create_object("rectangle", rect=(100, 10, 150, 40), colour=1))
    b = run(lambda: doc.create_object("rectangle", rect=(200, 60, 280, 90), colour=2))
    c = run(lambda: doc.create_object("rectangle", rect=(13, 0, 23, 10), colour=3))
    run(lambda: doc.align([a, b], "left"))
    run(lambda: doc.distribute([a, b, c], "h"))
    rect = _first(doc, (K.RECTANGLE,))
    run(lambda: doc.set_colour(rect, 0x123456))
    run(lambda: doc.set_line_width(rect, 3))
    run(lambda: doc.set_line_style(rect, 1))
    run(lambda: doc.set_filled(rect, False))
    t = _first(doc, (K.TEXT,))
    run(lambda: doc.set_text(t, "Caption edited"))
    run(lambda: doc.set_font(t, bold=True, size=14))
    run(lambda: doc.set_colour(t, 0x00FF00))
    run(lambda: doc.set_colour(_first(doc, (K.PIPE,), root=False), 0xFF8000))
    run(lambda: doc.set_property(len(doc.objects) - 4, "TXT_STR", "Tag2"))
    run(lambda: doc.set_page_background(0x00C0C0C0))
    run(lambda: doc.move_objects([genie()], 9, 4))
    run(resize_genie)
    run(doc.undo)
    run(member_colour)
    run(lambda: doc.duplicate_objects([len(doc.objects) - 2]))
    grp = run(lambda: doc.group_objects([len(doc.objects) - 1, len(doc.objects) - 3]))
    run(lambda: doc.ungroup_object(grp))
    run(doc.undo)
    run(lambda: doc.rotate_objects([len(doc.objects) - 5]))
    run(lambda: doc.ensure_property(len(doc.objects) - 6, "COMMENT", "wp29"))
    run(batch)
    run(lambda: doc.delete_objects([mid_root()]))
    for _ in range(5):
        run(doc.undo)
    for _ in range(3):
        run(doc.redo)
    return log


@pytest.mark.parametrize("src", [SHAPES, LARGE, GENIES, TPL_PAGE])
def test_save_bytes_identical_to_the_temp_file_path(src, tmp_path, monkeypatch):
    out = []
    for name in ("old", "new"):
        d = tmp_path / name
        d.mkdir()
        doc = _open_copy(src, d, monkeypatch)
        if name == "old":
            doc._old_tmp = str(d)
            doc._rerender_page = types.MethodType(_old_rerender, doc)
        log = _edit_set(doc)
        out.append((doc.serialize(), [o.kind for o in doc.page.objects], log))
    assert out[0][2] == out[1][2]
    assert sum(1 for r, _v in out[1][2] if r == "ok") > 40
    assert out[0][0] == out[1][0]
    assert out[0][1] == out[1][1]


# --------------------------------------------------------------------------- %TEMP%
def test_edits_write_nothing_to_temp_and_survive_an_unwritable_temp(tmp_path, monkeypatch):
    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))
    rect = _first(doc, (K.RECTANGLE,))
    doc.set_colour(rect, 0x112233)
    doc.set_text(_first(doc, (K.TEXT,)), "No temp")
    n = doc.create_object("rectangle", rect=(5, 5, 50, 50), colour=0xFF0000)
    doc.delete_objects([n])
    doc.undo()
    doc.undo()
    doc.redo()
    assert not list(temp.iterdir())

    # %TEMP% unwritable (a disk-full drive): every temp-file API fails
    def fail(*_a, **_k):
        raise OSError(28, "No space left on device")

    blocked = tmp_path / "file.txt"
    blocked.write_text("x")
    monkeypatch.setattr(tempfile, "tempdir", str(blocked / "sub"))
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(blocked / "sub"))
    for fn in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile"):
        monkeypatch.setattr(tempfile, fn, fail)
    doc.set_colour(rect, 0x445566)
    doc.set_line_width(rect, 2)
    doc.create_object("ellipse", rect=(60, 60, 90, 90), colour=0x00FF00)
    doc.undo()
    doc.redo()
    assert reader.model_difference(doc.page, _full(doc)) == ""


# --------------------------------------------------------------------------- speed
def test_body_edit_on_a_5000_object_page_is_fast(tmp_path, monkeypatch):
    doc = _open_copy(HUGE, tmp_path, monkeypatch)
    assert len(doc.objects) >= 4900
    rect = _first(doc, (K.RECTANGLE,))
    text = _first(doc, (K.TEXT,))
    times = []
    for k, edit in enumerate(
        (
            lambda: doc.set_colour(rect, 0x102030),
            lambda: doc.set_line_width(rect, 2),
            lambda: doc.set_text(text, "Fast"),
            lambda: doc.set_colour(rect, 0x302010),
            doc.undo,
            doc.undo,
            doc.redo,
        )
    ):
        t = time.perf_counter()
        edit()
        times.append(time.perf_counter() - t)
    # acceptance: < 150 ms per body edit
    assert sorted(times)[len(times) // 2] < 0.15, times
    assert max(times) < 0.5, times


def test_eighteen_step_undo_on_the_big_page_is_fast(tmp_path, monkeypatch):
    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    rects = [
        i for i, o in enumerate(doc.page.objects) if o.kind is K.RECTANGLE and doc.objects[i].subtype_size & 2
    ]
    for k in range(6):
        doc.create_object("rectangle", rect=(10 * k, 10, 10 * k + 8, 18), colour=k)
    k = 0
    while len(doc._undo) < 18:  # colour + line-width edits, one step each
        i = rects[k % len(rects)]
        if k % 2:
            doc.set_line_width(i, 1 + (k % 5))
        else:
            doc.set_colour(i, 0x010101 * (k + 1))
        k += 1
    steps = 18
    t = time.perf_counter()
    for _ in range(steps):
        doc.undo()
    took = time.perf_counter() - t
    check_speed(took, 1.0, "18 undo steps")  # acceptance
    assert reader.model_difference(doc.page, _full(doc)) == ""


# --------------------------------------------------------------------------- scene / tree
def _stack(scene):
    """The scene's top-level items in stacking order, as comparable rows."""
    from PySide6 import QtCore

    mine = {id(it): ("item", i) for i, it in scene._items.items()}
    mine.update({id(it): ("ai", i) for i, it in scene._ai_items.items()})
    rows = []
    for it in scene.items(QtCore.Qt.AscendingOrder):
        key = mine.get(id(it))
        if key is None:
            continue
        r = it.sceneBoundingRect()
        rows.append(
            (
                key,
                type(it).__name__,
                it.zValue(),
                it.isVisible(),
                round(r.x(), 3),
                round(r.y(), 3),
                round(r.width(), 3),
                round(r.height(), 3),
                len(it.childItems()),
            )
        )
    return rows


def test_scene_patch_equals_a_full_rebuild(tmp_path, monkeypatch):
    _app()
    from ctgedit.render.scene import PageScene

    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    scene = PageScene()
    scene.patch_limit = 1.0  # (a template unit may be most of a page)
    scene.set_page(doc.page)
    serial = doc.derive_serial
    rect = _first(doc, (K.RECTANGLE,))
    tpl = next(
        i
        for i, o in enumerate(doc.page.objects)
        if o.raw.get("_from_template") and o.parent is not None and o.kind is K.RECTANGLE
    )
    doc.set_colour(rect, 0x123456)
    assert doc.ensure_property(tpl, "COMMENT", "wp29")  # a unit of the page template
    g = next(
        i
        for i, o in enumerate(doc.page.objects)
        if o.parent is None and o.members and not o.raw.get("_from_template")
    )
    doc.move_objects([g], 5, 5)  # in place, not derived yet
    changes = doc.model_changes_since(serial)
    assert changes == {rect, doc.page.root_of(tpl), g}
    assert scene.update_units(doc.page, changes)
    fresh = PageScene()
    fresh.set_page(doc.page)
    assert _stack(scene) == _stack(fresh)


def test_page_background_change_rebuilds_every_item(tmp_path, monkeypatch):
    """A page-level edit changes no record, but the item builders read the page
    colour and size (an embossed text's body is the page colour): the scene must not
    keep the items built for the old page."""
    _app()
    from PySide6 import QtCore
    from ctgedit.render.scene import PageScene

    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    w, h = doc.page.appearance.width or 1024, doc.page.appearance.height or 768
    # a big panel with a low-contrast grey gradient (0x707070 -> 0x909090)
    r = doc.create_object("rectangle", rect=(10, 10, 10 + w // 3, 10 + h // 3), colour=0x707070)
    assert doc.set_gradient(r, 3, 0x909090)
    doc.set_page_background(0x00808080)  # the stops average to the page grey
    scene = PageScene()
    scene.set_page(doc.page)
    serial = doc.derive_serial
    doc.set_page_background(0x00FFFFFF)  # no record changed
    changes = doc.model_changes_since(serial)
    assert changes == set()
    assert not scene.update_units(doc.page, changes)  # declines: rebuild it all
    scene.set_page(doc.page)
    assert scene._items[r].brush().style() == QtCore.Qt.LinearGradientPattern
    # unchanged page state: the (empty) patch applies
    assert scene.update_units(doc.page, set())


def test_main_window_refresh_patches_scene_and_tree(tmp_path, monkeypatch):
    _app()
    from ctgedit.ui.main_window import MainWindow

    dst = tmp_path / "FxLarge.ctg"
    shutil.copy2(_path(LARGE), dst)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(LARGE)))
    w = MainWindow()
    try:
        w.open_document(str(dst))
        doc = w.doc
        text = _first(doc, (K.TEXT,))
        items_before = dict(w.scene._items)
        rows_before = dict(w.objects_dock._items)
        doc.set_text(text, "Patched")
        w._after_edit()
        # only the edited unit's scene items and tree rows were rebuilt
        assert text in doc._derivation.changed_roots
        sub = {j for r in doc._derivation.changed_roots for j in doc.page.subtree(r)}
        assert all(
            w.scene._items[i] is it for i, it in items_before.items() if i not in sub and i in w.scene._items
        )
        assert all(w.objects_dock._items[i] is it for i, it in rows_before.items() if i not in sub)
        assert "Patched" in w.objects_dock._items[text].text(0)
        # ... and both equal a full rebuild
        from ctgedit.render.scene import PageScene

        fresh = PageScene()
        fresh.set_page(doc.page, grid_size=w._grid_size if w._show_grid else 0)
        fresh.set_hidden(w._hidden_roots)
        assert _stack(w.scene) == _stack(fresh)
        tree = [(i, it.text(0)) for i, it in sorted(w.objects_dock._items.items())]
        w.objects_dock.set_page(doc.page, [], hidden=w._hidden_roots)
        assert tree == [(i, it.text(0)) for i, it in sorted(w.objects_dock._items.items())]
        # a structural edit rebuilds everything
        doc.delete_objects([text])
        w._after_edit()
        assert w.scene.shown[:2] == (doc, doc.derive_serial)
        assert _stack(w.scene) == _stack_fresh(doc, w)
    finally:
        w.doc.dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def test_scene_and_tree_patch_appended_and_removed_units(tmp_path, monkeypatch):
    """Create an object (appended at the top of the z-order), undo it, delete
    a unit near the end: each is a patch of the tail, equal to a rebuild."""
    _app()
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    doc = _open_copy(LARGE, tmp_path, monkeypatch)
    scene, dock = PageScene(), ObjectsDock()
    scene.set_page(doc.page)
    dock.set_page(doc.page, [])
    kept = dict(scene._items)

    def step(edit):
        serial = doc.derive_serial
        edit()
        changes = doc.model_changes_since(serial)
        assert changes is not None
        assert scene.update_units(doc.page, changes)
        assert dock.update_units(doc.page, changes, [])
        fresh, fdock = PageScene(), ObjectsDock()
        fresh.set_page(doc.page)
        fdock.set_page(doc.page, [])
        assert _stack(scene) == _stack(fresh)
        assert [(i, it.text(0)) for i, it in sorted(dock._items.items())] == [
            (i, it.text(0)) for i, it in sorted(fdock._items.items())
        ]
        assert dock._tree.topLevelItemCount() == fdock._tree.topLevelItemCount()

    step(lambda: doc.create_object("rectangle", rect=(20, 20, 80, 60), colour=0xFF0000))
    step(lambda: doc.create_object("text", rect=(20, 70, 90, 86), text="Tail", colour=0))
    step(doc.undo)
    step(doc.undo)
    roots = doc.page.roots()
    step(lambda: doc.delete_objects([roots[-3]]))
    step(doc.undo)
    # the units before the tail kept their items
    first = doc.page.roots()[len(doc.page.roots()) // 2]
    assert all(scene._items.get(i) is it for i, it in kept.items() if i < first)


def test_tree_patch_keeps_the_expanded_unit_not_the_index(tmp_path, monkeypatch):
    """A tree patch keeps the expanded unit, not the index."""
    _app()
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    doc = _open_copy(GENIES, tmp_path, monkeypatch)
    dock = ObjectsDock()
    dock.set_page(doc.page, [])
    groups = [
        r
        for r in doc.page.roots()
        if doc.page.objects[r].members
        and not doc.page.objects[r].raw.get("_from_template")
        and not doc.page.objects[r].raw.get("_non_drawable")
    ]

    def victim_of(g):
        return max(r for r in doc.page.roots() if r < g and not doc.page.objects[r].raw.get("_from_template"))

    # a group with a root before it and another after it, near enough to the end that
    # the rows from the deleted unit on stay under the dock's patch share (the patch path
    # this is about; past it the dock sets the rows again)
    n = len(doc.page.objects)
    limit = max(200, int(n * dock.PATCH_SHARE))
    cands = [g for g in groups[1:-1] if n - victim_of(g) <= limit]
    assert cands
    g = cands[len(cands) // 2]
    victim = victim_of(g)
    obj = doc.page.objects[g]
    dock._items[g].setExpanded(True)
    serial = doc.derive_serial
    doc.delete_objects([victim])
    assert dock.update_units(doc.page, doc.model_changes_since(serial), [])
    now = next(i for i, o in enumerate(doc.page.objects) if o is obj)
    assert now < g and dock._items[now].isExpanded()
    assert g not in dock._items or not dock._items[g].isExpanded() or doc.page.objects[g] is obj
    # an edit inside the expanded unit derives it afresh at the same index
    serial = doc.derive_serial
    assert doc.ensure_property(doc.page.objects[now].members[0], "COMMENT", "wp29")
    assert doc.page.objects[now] is not obj
    assert dock.update_units(doc.page, doc.model_changes_since(serial), [])
    assert dock._items[now].isExpanded()


def test_random_ui_session_patches_equal_rebuilds(tmp_path, monkeypatch):
    """60 random edits through the window: after every refresh the patched
    scene and Objects tree equal a rebuild from scratch."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    dst = tmp_path / os.path.basename(_path(SA_PAGE))
    shutil.copy2(_path(SA_PAGE), dst)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(SA_PAGE)))
    w = MainWindow()
    try:
        w.open_document(str(dst))
        doc = w.doc
        rng = random.Random(7)
        full = [0]
        orig = w.scene.set_page

        def counting(*a, **k):
            full[0] += 1
            return orig(*a, **k)

        w.scene.set_page = counting
        for _ in range(60):
            _session(doc, rng, 1)
            w._after_edit()
            assert _stack(w.scene) == _stack_fresh(doc, w)
            fdock = ObjectsDock()
            fdock.set_page(doc.page, [])
            assert [(i, it.text(0)) for i, it in sorted(w.objects_dock._items.items())] == [
                (i, it.text(0)) for i, it in sorted(fdock._items.items())
            ]
        assert full[0] < 20  # most refreshes patched
    finally:
        w.doc.dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def _stack_fresh(doc, w):
    from ctgedit.render.scene import PageScene

    fresh = PageScene()
    fresh.set_page(doc.page, grid_size=w._grid_size if w._show_grid else 0)
    fresh.set_hidden(w._hidden_roots)
    return _stack(fresh)


def test_the_undo_history_is_capped_by_memory(tmp_path, monkeypatch):
    """The undo history is capped by memory."""
    import shutil
    from ctgedit import editing as E

    src = _path(LARGE)
    dst = str(tmp_path / "FxLarge.ctg")
    shutil.copyfile(src, dst)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(src))
    doc = E.EditableDocument.open(dst)
    big = max(doc.page.roots(), key=lambda r: len(doc.page.subtree(r)))
    doc.move_objects([big], 1, 0)
    one = E.command_bytes(doc._undo[-1])
    assert one > 3000
    monkeypatch.setattr(E, "UNDO_BYTES_TOTAL", one * 15)
    for _k in range(30):
        doc.move_objects([big], 1, 0)
    assert E.UNDO_KEEP_MIN <= len(doc._undo) <= 16
    assert doc.undo_trimmed >= 15
    before = doc.serialize()
    mark = doc.history_mark()
    for _k in range(20):
        doc.move_objects([big], 0, 1)
    doc.revert_to_mark(mark)
    assert doc.serialize() == before


def test_a_move_and_its_undo_redo_shift_the_items_instead_of_rebuilding(tmp_path, monkeypatch):
    """A move (and its undo / redo) of a unit moves its scene items and leaves the Objects
    tree alone; the scene then equals a full rebuild."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    dst = tmp_path / "FxLarge.ctg"
    shutil.copy2(_path(LARGE), dst)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(LARGE)))
    w = MainWindow()
    try:
        w.open_document(str(dst))
        doc = w.doc
        big = max(
            (r for r in doc.page.roots() if not doc.page.objects[r].raw.get("_from_template")),
            key=lambda r: len(doc.page.subtree(r)),
        )
        calls = []
        orig_set, orig_upd = w.scene.set_page, w.scene.update_units
        monkeypatch.setattr(w.scene, "set_page", lambda *a, **k: calls.append("set") or orig_set(*a, **k))
        monkeypatch.setattr(w.scene, "update_units", lambda *a, **k: calls.append("upd") or orig_upd(*a, **k))
        tree_calls = []
        monkeypatch.setattr(w.objects_dock, "set_page", lambda *a, **k: tree_calls.append("set"))
        monkeypatch.setattr(w.objects_dock, "update_units", lambda *a, **k: tree_calls.append("upd") or True)
        w._on_objects_moved([big], 7, -3)
        assert calls == [] and _stack(w.scene) == _stack_fresh(doc, w)
        w.undo()
        assert calls == [] and tree_calls == [] and _stack(w.scene) == _stack_fresh(doc, w)
        w.redo()
        assert calls == [] and tree_calls == [] and _stack(w.scene) == _stack_fresh(doc, w)
        # an edit that is not a move goes the normal way again
        doc.set_page_background(0x00FFFFFF)
        w._after_edit()
        assert calls and _stack(w.scene) == _stack_fresh(doc, w)
    finally:
        w.doc.dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def test_a_tab_keeps_its_scene_and_tree_rows(tmp_path, monkeypatch):
    """Switching back to a tab shows its own scene and Objects tree rows as they were, no
    rebuild; they equal a rebuild."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    a, b = tmp_path / "FxLarge.ctg", tmp_path / "FxGenies.ctg"
    shutil.copy2(_path(LARGE), a)
    shutil.copy2(_path(GENIES), b)
    # (FxGenies is FxMain's; the libraries of both are on FxPerf's chain too)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(LARGE)))
    w = MainWindow()
    try:
        w.open_document(str(a))
        scene_a = w.scene
        w.open_document(str(b))
        assert w.scene is not scene_a
        calls = []
        orig = w.objects_dock.set_page
        monkeypatch.setattr(w.objects_dock, "set_page", lambda *x, **k: calls.append(1) or orig(*x, **k))
        w.doc_tabs.setCurrentIndex(0)
        assert w.scene is scene_a and calls == []
        assert _stack(w.scene) == _stack_fresh(w.doc, w)
        rows = sorted((i, it.text(0)) for i, it in w.objects_dock._items.items())
        orig(w.doc.page, [], hidden=w._hidden_roots)
        assert rows == sorted((i, it.text(0)) for i, it in w.objects_dock._items.items())
    finally:
        for e in w._docs:
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def test_move_undo_keeps_only_the_changed_blocks_and_restores_the_bytes(tmp_path, monkeypatch):
    """A move's undo keeps only the changed blocks and restores the bytes."""
    import shutil
    from ctgedit import editing as E
    from ctgedit.editing_base import command_bytes

    p = str(tmp_path / "FxLarge.ctg")
    shutil.copyfile(_path(LARGE), p)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(LARGE)))
    doc = E.EditableDocument.open(p)
    groups = []
    for k in range(5):
        ids = []
        for j in range(4):
            i = doc.create_object(
                "rectangle", rect=(20 + 60 * j, 900 + 40 * k, 60 + 60 * j, 930 + 40 * k), colour=0x336699
            )
            assert doc.ensure_property(i, "COMMENT", ("fx%d%d " % (k, j)) * 400)
            ids.append(i)
        groups.append(doc.group_objects(ids))
    doc.save()
    doc = E.EditableDocument.open(p)
    before = doc.serialize()
    roots = [i for i, o in enumerate(doc.parsed.page.objects) if o.class_flag > 3][-5:]
    assert len(roots) == 5 and all(len(doc.page.subtree(r)) == 5 for r in roots)
    idx = sorted({j for r in roots for j in doc.page.subtree(r)})
    body = sum(len(doc.parsed.page.objects[i].body_raw) for i in idx)
    doc.move_objects(roots, 7, 3)
    kept = command_bytes(doc._undo[-1])
    assert kept < body / 2, (kept, body)
    doc.undo()
    assert doc.serialize() == before
    doc.redo()
    doc.undo()
    assert doc.serialize() == before


def test_one_undo_budget_for_all_open_documents(tmp_path, monkeypatch):
    """Past UNDO_BYTES_TOTAL together, the oldest steps of the document with the largest
    history go (each keeps its newest UNDO_KEEP_MIN steps)."""
    import shutil
    from ctgedit import editing as E

    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(LARGE)))
    docs = []
    for name in ("a.ctg", "b.ctg"):
        p = str(tmp_path / name)
        shutil.copyfile(_path(LARGE), p)
        docs.append(E.EditableDocument.open(p))
    a, b = docs
    roots = [i for i, o in enumerate(a.parsed.page.objects) if o.class_flag > 3][:3]
    for k in range(E.UNDO_KEEP_MIN + 8):
        a.move_objects(roots, 1, 0)
    for k in range(3):
        b.move_objects(roots, 1, 0)
    monkeypatch.setattr(E, "UNDO_BYTES_TOTAL", E._undo_total(b) + 1)
    b.move_objects(roots, 1, 0)
    assert len(a._undo) == E.UNDO_KEEP_MIN
    assert len(b._undo) == 4


def test_a_moves_redo_puts_the_moved_records_back_without_moving_again(tmp_path, monkeypatch):
    """Redo of a move puts back the records' states after the move: the same bytes as the
    move gave, the model as a full derive gives, and undo / redo again."""
    import shutil
    from ctgedit import editing as E
    from ctgedit.format import placement

    p = str(tmp_path / "FxLarge.ctg")
    shutil.copyfile(_path(LARGE), p)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(LARGE)))
    doc = E.EditableDocument.open(p)
    doc.derive_check = True  # every re-derive == a full one
    before = doc.serialize()
    roots = [i for i, o in enumerate(doc.parsed.page.objects) if o.class_flag > 3][:5]
    doc.move_objects(roots, 7, 3)
    moved = doc.serialize()
    geo = [(o.geometry.x, o.geometry.y, o.geometry.extent_x, o.geometry.extent_y) for o in doc.page.objects]
    calls = []
    real = placement.translate_records
    monkeypatch.setattr(placement, "translate_records", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(2):
        doc.undo()
        assert doc.serialize() == before
        doc.redo()
        assert doc.serialize() == moved
        assert [
            (o.geometry.x, o.geometry.y, o.geometry.extent_x, o.geometry.extent_y) for o in doc.page.objects
        ] == geo
    assert not calls  # redo did not move the records again
    doc._derive()  # (derive_check: the incremental == a full one)


def test_a_genies_undo_after_its_save_rewrote_the_records_still_works(tmp_path, monkeypatch):
    """A genie's undo still works after its save rewrote the records."""
    import shutil
    from ctgedit import editing as E

    lib = _FX[0].file("FxLib", "fxgenie.ctm")
    p = str(tmp_path / "fxgenie.ctm")
    shutil.copyfile(lib, p)
    from ctgedit.format import symbols as S

    name = S.open_library(p).element_names()[0]
    e = E.ElementDocument.open_element(p, name)
    before = [bytes(o.body_raw) for o in e.parsed.page.objects]
    roots = list(e.page.roots())
    e.move_objects([roots[0]], 6, 3)
    # what the save's in-place patch can do: a moved record's body changes length
    moved = [i for i, o in enumerate(e.parsed.page.objects) if bytes(o.body_raw) != before[i]]
    assert moved
    po = e.parsed.page.objects[moved[0]]
    po.body_raw = bytes(po.body_raw) + b"\0\0"
    e.undo()
    assert [bytes(o.body_raw) for o in e.parsed.page.objects] == before
    # a page keeps changed blocks: a rewritten record makes its undo refuse, untouched
    from ctgedit.doc.geometry import _BlockDiff

    q = str(tmp_path / "FxLarge.ctg")
    shutil.copyfile(_path(LARGE), q)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(LARGE)))
    doc = E.EditableDocument.open(q)
    big = [i for i, o in enumerate(doc.parsed.page.objects) if o.class_flag > 3][:3]
    doc.move_objects(big, 7, 3)
    step = doc._undo[-1]
    states = next(
        c.cell_contents
        for c in step.undo.__closure__
        if isinstance(c.cell_contents, list) and c.cell_contents and isinstance(c.cell_contents[0], tuple)
    )
    victim = next(o for o, st, _a in states if isinstance(st.get("body_raw"), _BlockDiff))
    victim.body_raw = bytes(victim.body_raw) + b"\0"
    snap = [bytes(o.body_raw) for o in doc.parsed.page.objects]
    with pytest.raises(ValueError):
        doc.undo()
    assert [bytes(o.body_raw) for o in doc.parsed.page.objects] == snap


def _views_seen(w) -> list:
    """What the window's scene and Objects tree hold, to compare two sessions: the items in
    stacking order with their keys, the item maps' order, every row of the tree (text, tip,
    index, eye, open, hidden, selected), the current row, the rows' ranks, the scroll place."""
    from PySide6 import QtCore
    from ctgedit.render.scene import PAGE_INDEX_ROLE

    sc, dock = w.scene, w.objects_dock
    items = [
        (
            type(it).__name__,
            it.data(0),
            it.data(PAGE_INDEX_ROLE),
            it.isVisible(),
            tuple(round(v, 2) for v in it.sceneBoundingRect().getRect()),
        )
        for it in sc.items(QtCore.Qt.AscendingOrder)
    ]
    rows = []

    def walk(it, depth):
        rows.append(
            (
                depth,
                it.text(0),
                it.toolTip(0),
                it.data(0, QtCore.Qt.UserRole),
                it.checkState(0).value if it.parent() is None else None,
                it.isExpanded(),
                it.isHidden(),
                it.isSelected(),
            )
        )
        for k in range(it.childCount()):
            walk(it.child(k), depth + 1)

    tree = dock._tree
    for k in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(k), 0)
    cur = tree.currentItem()
    return [
        items,
        list(sc._items),
        list(sc._ai_items),
        rows,
        cur.data(0, QtCore.Qt.UserRole) if cur is not None else None,
        sorted(dock._rank, key=dock._rank.get),
        list(dock._items),
        tree.verticalScrollBar().value(),
        sc.sceneRect().getRect(),
    ]


@pytest.mark.parametrize("src", [SA_PAGE, LARGE])
def test_kept_units_give_the_scene_and_tree_a_rebuild_gives(src, tmp_path, monkeypatch):
    """A unit that only moved to another index (a delete or an insert before it, a reorder)
    keeps its scene items and tree rows under the new indices (KEEP_UNITS): over 80 random
    edits through the window, with open rows and a selection, the scene and the Objects
    tree hold exactly what building those units again gives."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.window.view import ViewState

    seen = []
    for keep in (False, True):
        dst = tmp_path / ("k%d" % keep) / os.path.basename(_path(src))
        dst.parent.mkdir()
        shutil.copy2(_path(src), dst)
        monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(_path(src)))
        monkeypatch.setattr(ViewState, "KEEP_UNITS", keep)
        w = MainWindow()
        try:
            w.open_document(str(dst))
            doc = w.doc
            rng = random.Random(11)
            out = []
            for k in range(80):
                if k % 9 == 0:
                    for r in doc.page.roots()[::7]:
                        it = w.objects_dock._items.get(r)
                        if it is not None and doc.page.objects[r].members:
                            it.setExpanded(True)
                    roots = doc.page.roots()
                    w._on_canvas_selection([roots[len(roots) // 3]])
                if k % 10 == 3:
                    # (a change of most of the page: a delete near the start, a unit sent
                    # to the back, their undo)
                    roots = doc.page.roots()
                    edit = rng.choice(
                        [
                            lambda: doc.delete_objects([roots[2]]),
                            lambda: doc.reorder([roots[-2]], "back"),
                            lambda: doc.reorder([roots[1]], "front"),
                        ]
                    )
                    try:
                        edit()
                    except Exception:  # noqa: BLE001 -- (a template record: not edited)
                        pass
                    w._after_edit()
                    out.append(_views_seen(w))
                    doc.undo()
                    w._after_edit()
                    out.append(_views_seen(w))
                _session(doc, rng, 1)
                w._after_edit()
                out.append(_views_seen(w))
            seen.append(out)
        finally:
            w.doc.dirty = False
            if w in MainWindow.instances:
                MainWindow.instances.remove(w)
    for step, (a, b) in enumerate(zip(*seen)):
        assert a == b, "step %d differs" % step
