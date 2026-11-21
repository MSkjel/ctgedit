"""A long session ends up the same as a fresh editor."""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "session_soak.py")
#: the session's pages (FxMain's): genies, every fxparts genie, groups and sets, symbols
PAGES = ("FxGenies", "FxParts", "FxArrange", "FxSymbols")

heavy = pytest.mark.skipif(
    os.environ.get("CTG_SKIP_HEAVY") == "1", reason="heavy (a ~40 s editor session): CTG_SKIP_HEAVY=1"
)


def _soak(fx_install, tmp_path, *extra):
    """The soak over PAGES of the fx_install copy, in its own process (CTG_SCADA_ROOT, set
    by the fixture, names the copy there too)."""
    out = tmp_path / "soak.json"
    cache = os.environ.get("CTG_SOAK_CACHE") or str(tmp_path / "baselines")
    env = dict(os.environ, PYTHONPATH=ROOT)
    pages = ",".join(fx_install.page(p) for p in PAGES)
    r = subprocess.run(
        [
            sys.executable,
            TOOL,
            "--pages",
            pages,
            "--cache",
            cache,
            "--out",
            str(out),
            "--shots",
            str(tmp_path / "shots"),
        ]
        + list(extra),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )
    assert out.is_file(), "the soak did not finish:\n%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:])
    res = json.loads(out.read_text(encoding="utf-8"))
    return r, res


def _assert_clean(r, res):
    assert res["fatal"] is None, res["fatal"]
    # the session really exercised the comparisons
    assert res["checks"] >= 12, (res["checks"], r.stdout[-2000:])
    assert res["render_compared"] >= 8, res["render_compared"]
    failed = [
        c
        for c in res["check_list"]
        if False in (c.get("model_ok"), c.get("drawn_ok"), c.get("render_ok"), c.get("bytes_ok"))
    ]
    assert not failed, json.dumps(failed[:5], indent=1)
    assert not res["findings"], json.dumps(res["findings"][:5], indent=1, default=str)
    assert r.returncode == 0, r.stdout[-2000:]


#: the operations of these runs (every one, the File > Open preview overlaps included)
OPS = (
    "open,open,switch,close,reopen,revert,genie_dialog,genie_ok,move,rotate,"
    "edit_definition,refresh,library_refresh,undo_redo,preview_overlap,preview_same,"
    "preview_same_cold"
)


@heavy
def test_session_matches_fresh_editor(fx_install, tmp_path):
    r, res = _soak(fx_install, tmp_path, "--seed", "2", "--steps", "30", "--budget-s", "40", "--ops", OPS)
    _assert_clean(r, res)


@heavy
def test_session_with_tiny_caches_matches_fresh_editor(fx_install, tmp_path):
    r, res = _soak(
        fx_install,
        tmp_path,
        "--seed",
        "3",
        "--steps",
        "30",
        "--budget-s",
        "40",
        "--ops",
        OPS,
        "--small-caches",
    )
    _assert_clean(r, res)


# -- Undo gives the file's bytes back -------------------------------------------------------
UNDO_CASES = [
    pytest.param("FxGenies", None, id="FxGenies-every-unit"),
    pytest.param("FxParts", None, id="FxParts-every-unit"),
    pytest.param("FxArrange", None, id="FxArrange-every-unit"),
    pytest.param("FxGenies", "fx_lamp", id="FxGenies-fx_lamp"),
]


@pytest.mark.parametrize("page,element", UNDO_CASES)
def test_move_and_rotate_undo_restore_the_file_bytes(fx_page, page, element):
    """Move + Undo and Rotate + Undo of a top-level unit leave the page byte-identical to the
    file (``element`` = only the placed genies of that element, else every top-level unit)."""
    path = fx_page(page)
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(path)
    with open(path, "rb") as fh:
        original = fh.read()
    assert doc.serialize() == original
    if element:
        roots = [i for i, (_lib, el) in doc.genie_instances() if el.lower() == element]
    else:
        roots = [
            i for i, o in enumerate(doc.page.objects) if o.parent is None and not o.raw.get("_from_template")
        ]
    assert roots
    bad = []
    for r in roots:
        for op in ("move", "rotate"):
            if op == "move":
                doc.move_objects([r], 8, 0)
            else:
                doc.rotate_objects([r], clockwise=True)
            doc.undo()
            if doc.serialize() != original:
                bad.append((op, r))
                doc = EditableDocument.open(path)
    assert not bad, bad


def test_a_stored_scaled_slot_survives_move_undo(fx_page):
    """A stored scaled slot survives move and undo."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import placement as PL

    path = fx_page("FxScaled")
    doc = EditableDocument.open(path)
    with open(path, "rb") as fh:
        original = fh.read()
    assert doc.serialize() == original
    (root,) = doc.find_objects_by_name("FxScaledSet")
    objs = doc.parsed.page.objects
    sset = next(j for j in doc.page.subtree(root) if objs[j].type_code == 0x0C)
    (_s1, s2, s3), _end = PL.read_slots(objs[sset].body_raw)
    for slot in (s2, s3):
        assert (round(slot[1][0], 2), round(slot[1][3], 2)) == (0.80, 0.77)
    roots = [i for i, o in enumerate(doc.page.objects) if o.parent is None]
    assert len(roots) == 3
    edits = [
        ("move", lambda: doc.move_objects([root], 8, 3)),
        ("rotate", lambda: doc.rotate_objects([root], clockwise=True)),
        ("align", lambda: doc.align(roots, "left")),
        ("distribute", lambda: doc.distribute(roots, "h")),
    ]
    for name, edit in edits:
        edit()
        forward = doc.serialize()
        assert forward != original, name
        doc.undo()
        assert doc.serialize() == original, name
        doc.redo()
        assert doc.serialize() == forward, name
        doc.undo()
        assert doc.serialize() == original, name


def test_move_align_distribute_undo_redo_give_the_same_bytes(fx_page):
    """Undo of a move / align / distribute puts the stored bytes back, and Redo gives the
    forward edit's bytes again: FxGenies' FxLamp (fx_lamp: a symbol set on %Tag%) with the
    page's other top-level units."""
    path = fx_page("FxGenies")
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(path)
    with open(path, "rb") as fh:
        original = fh.read()
    lamp = [i for i, (_lib, el) in doc.genie_instances() if el.lower() == "fx_lamp"]
    assert lamp == doc.find_objects_by_name("FxLamp")
    roots = [
        i for i, o in enumerate(doc.page.objects) if o.parent is None and not o.raw.get("_from_template")
    ]
    edits = [
        ("move", lambda: doc.move_objects(lamp[:1], 8, 3)),
        ("align", lambda: doc.align(lamp + roots[:2], "left")),
        ("distribute", lambda: doc.distribute(lamp + roots[:3], "h")),
    ]
    for name, edit in edits:
        edit()
        forward = doc.serialize()
        assert forward != original, name
        doc.undo()
        assert doc.serialize() == original, name
        doc.redo()
        assert doc.serialize() == forward, name
        doc.undo()
        assert doc.serialize() == original, name
