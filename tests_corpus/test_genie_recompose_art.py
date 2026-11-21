"""A recomposed genie still draws its art, on the real corpus."""

import importlib.util
import os

import pytest

import corpus

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


def _sweep():
    spec = importlib.util.spec_from_file_location(
        "genie_recompose_sweep", os.path.join(TOOLS, "genie_recompose_sweep.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _sweep()

#: (page, library, element) -- small genies whose members take their art from owner / twin
#: records (Example)
CASES = [
    ("Example/Mixer.ctg", "energydemo", "agitator_mixer"),
    ("Example/Mixer.ctg", "energydemo", "screw_left"),
    ("Example/Genies.ctg", "xp_controls", "button"),
    ("Example/Genies.ctg", "switches", "toggleknobswitch"),
]


def _ids(cases, part):
    """The test ids: the element, after the page name's tail when ``part`` is 'page'; the
    skipped parameter set of a role that is not set is 'not-configured'."""
    out = []
    for c in cases:
        if hasattr(c, "marks"):  # (pytest.param)
            out.append("not-configured")
        elif part == "page":
            out.append("%s-%s" % (os.path.splitext(os.path.basename(c[0]))[0][-12:], c[2]))
        else:
            out.append(c[2])
    return out


_DOCS = {}


def _doc(rel):
    """The page ('Project/Page.ctg' in the install's User folder) opened once per module
    (every step below is undone)."""
    path = corpus.install_path(rel)
    if not os.path.isfile(path):
        pytest.skip("%s not installed" % rel)
    hit = _DOCS.get(path)
    if hit is None:
        from ctgedit.editing import EditableDocument

        doc = EditableDocument.open(path)
        hit = _DOCS[path] = (doc, doc.serialize())
    return hit


@pytest.fixture(scope="module", autouse=True)
def _drop_docs():
    yield
    _DOCS.clear()


def _instance(doc, library, element):
    for i, (lib, el) in doc.genie_instances():
        if lib.lower() == library.lower() and el.lower() == element.lower():
            return i
    pytest.skip("no top-level %s.%s on the page" % (library, element))


def _current_count(doc, root):
    """The record count of the instance's CURRENT element as a page composes it: a stored
    instance older than its element (its library edited since the page was saved) is
    re-composed to it (LIVE: CtDraw's load of a page gave its stale
    instance the 19 records of the element's new wrapper group, not its 18)."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import reference_from_object

    ref = reference_from_object(doc.parsed.page.objects[root])
    rr = doc._resolver().resolve(ref)
    return len(GP.load_element(rr.library_path, ref.element, compose=True).records)


def _run(doc, original, root, kind, fn):
    before = S.subtree_stats(doc.page, root)
    assert before["art"] > 0, "the stored instance draws no art -- not a useful case"
    res = S._step(doc, root, before, kind, fn, True, original)
    assert res["outcome"] != "error", res.get("error")
    assert res["outcome"] != "not-applicable", "the genie was not re-composed"
    if res["outcome"] == "changed":
        assert res["undo_restores_bytes"] and res["undo_restores_model"], res
        # what a reload of the re-composed page draws = what the session draws
        assert res["fresh_matches_session"], res
    assert doc.serialize() == original
    return before, res


@pytest.mark.parametrize("rel,library,element", CASES, ids=_ids(CASES, "page"))
def test_identity_recompose_keeps_every_art_member(rel, library, element):
    """Re-composing the instance with its own values (Update Pages / CtDraw's load) leaves
    every art-bearing member drawing its art -- and nothing drawn changes."""
    doc, original = _doc(rel)
    root = _instance(doc, library, element)
    want = _current_count(doc, root)
    before, res = _run(doc, original, root, "identity", lambda: doc._recompose_genie(root, {}, "identity"))
    if res["outcome"] == "changed":
        assert res["records"][1] == want, res
        assert res["art"][1] >= res["art"][0], res
        if res["records"][0] == want:
            # a current instance: nothing drawn changes (a stale one follows its element)
            assert res["art_lost_members"] == 0, res
            assert res.get("changed_members") == 0, res.get("changed_samples")


@pytest.mark.parametrize("rel,library,element", CASES, ids=_ids(CASES, "page"))
def test_parameter_change_keeps_the_art(rel, library, element):
    """The genie dialog's OK with one parameter changed (numeric +1, else 'X' appended)
    re-composes the instance: it keeps its members and every one of their art."""
    doc, original = _doc(rel)
    root = _instance(doc, library, element)
    pick = S.pick_parameter(doc, root, {})
    if pick is None:
        pytest.skip("the genie declares no parameters")
    name, _old, new = pick
    want = _current_count(doc, root)
    before, res = _run(doc, original, root, "param", lambda: doc.set_genie_bindings(root, {name: new}))
    assert res["outcome"] == "changed", "a changed parameter re-composed nothing"
    assert res["records"][1] == want, res
    assert res["art"][1] >= res["art"][0], res
    assert res["art_lost_members"] == 0, res
