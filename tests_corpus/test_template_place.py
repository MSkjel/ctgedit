"""Page templates over the real corpus."""

import glob
import os
import shutil

import pytest

from ctgedit.format import genie_place as GP
from ctgedit.format import geometry as G
from ctgedit.format import links
from ctgedit.format import template_place as TP
from ctgedit.format import writer
from ctgedit.format.page import parse_ctg
from ctgedit.format.symbols import SymbolResolver


def _payloads(objs):
    return [writer.object_payload(po) for po in objs]


def _corpus_check(pages):
    exact = current = 0
    resolvers = {}
    for p in pages:
        pc = parse_ctg(p)
        if pc.page is None:
            continue
        d = os.path.dirname(p)
        res = resolvers.get(d) or resolvers.setdefault(d, SymbolResolver.for_page(p))
        roots = TP.template_roots(pc.page.objects)
        if not roots:
            continue
        tpl = TP.resolve_template(res, roots[0][2])
        if tpl is None:
            continue
        link = GP.decode(pc.page.objects[roots[0][0]]).link or roots[0][2].raw
        if TP.is_stale(pc.page, tpl, link):
            continue
        current += 1
        r = TP.recompose(pc.page.objects, res, page_name=os.path.splitext(os.path.basename(p))[0])
        exact += _payloads(r["objects"]) == _payloads(pc.page.objects)
    return exact, current


def _engine_roundtrip():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "engine_roundtrip", os.path.join(os.path.dirname(__file__), "..", "tools", "engine_roundtrip.py")
    )
    ER = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ER)
    return ER


def _geo(po):
    gh = G.parse_geom_header(po.body_raw)
    return tuple(gh.anchor), tuple(gh.rect1), tuple(gh.rect2)


R2_USER = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"


@pytest.mark.skipif(
    os.environ.get("CTG_CORPUS_TESTS") != "1", reason="whole-corpus run (~15 min): set CTG_CORPUS_TESTS=1"
)
def test_recompose_reproduces_the_corpus():
    exact, current = _corpus_check(sorted(glob.glob(os.path.join(R2_USER, "*", "*.ctg"))))
    assert current >= 1600
    assert exact >= 1611, (exact, current)


R2_EXAMPLE = os.path.join(R2_USER, "Example")
NORMAL_HD1080 = "sxw_style_1.normal@HD1080.<ctt"


@pytest.mark.skipif(not os.path.isdir(R2_EXAMPLE), reason="R2 Example not installed")
def test_template_operations_on_the_corpus(tmp_path, monkeypatch):
    """Template operations on the corpus."""
    ER = _engine_roundtrip()
    monkeypatch.setenv("CTG_LIB_PATH", R2_EXAMPLE)
    for name in ("!PUMP_A_ACC", "Genies"):
        shutil.copy2(os.path.join(R2_EXAMPLE, name + ".ctg"), str(tmp_path / (name + ".ctg")))
    _m, _l, linked = ER.template_mutator("new", str(tmp_path / "!PUMP_A_ACC.ctg"), NORMAL_HD1080)
    objs = linked.page.objects
    assert _geo(objs[68]) == ((1392, 143), (1392, 125, 1495, 144), (1392, 125, 1495, 144))
    assert _geo(objs[71]) == ((9, 143), (9, 125, 112, 144), (9, 125, 112, 144))
    _m, _l, unl = ER.template_mutator("new_unlinked", str(tmp_path / "!PUMP_A_ACC.ctg"), NORMAL_HD1080)
    objs = unl.page.objects
    # (no root group on an unlinked page: the members are one record earlier)
    assert _geo(objs[67]) == ((1392, 143), (1392, 125, 1495, 144), (1392, 125, 1495, 144))
    assert _geo(objs[70]) == ((9, 143), (9, 125, 112, 144), (9, 125, 112, 144))
    _m, _l, cut = ER.template_mutator("unlink", str(tmp_path / "Genies.ctg"))
    names = [n for _g, n in links.parse_link_table(cut.page.link_table_raw)[1]]
    at = names.index("sxw_controls.hyperlink08.<ctm#de81318f")
    assert names[at : at + 7] == [
        "sxw_controls.hyperlink08.<ctm#de81318f",
        "\\\\Login...",
        "\\\\Print...",
        "sxw_controls.hyperlink09.<ctm#de81318f",
        "\\\\Home Page",
        "sxw_controls.Tab_Text.<ctm#de81318f",
        "\\\\Menu",
    ]
    keys = [k for k, _v in GP.decode(cut.page.objects[137]).entries[0][1].pairs]
    assert keys.index("VISIBLE") < keys.index("TXT_OFF")
    assert not any(
        ("EquipSymLib", "Equipment") in bg.pairs
        for po in cut.page.objects[1:704]
        if po.subtype_size & 0x8
        for lv in GP.decode(po).entries
        for bg in lv
    )
