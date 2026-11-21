"""Fill colours over the real corpus."""

import copy
import glob
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.format import colourpalette as P  # noqa: E402
from ctgedit.format import colourstate as CS  # noqa: E402
from ctgedit.format import fillcolour as F  # noqa: E402
from ctgedit.format import writer  # noqa: E402
from ctgedit.format.page import parse_ctg  # noqa: E402

try:
    from ctgedit.io.citect_config import live_project_dir

    EX = live_project_dir("Example")
    SA = live_project_dir("ExampleSA")
except Exception:  # noqa: BLE001
    EX = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"
    SA = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\ExampleSA"
MIXER = os.path.join(EX, "Mixer.ctg")
need = pytest.mark.skipif(not os.path.exists(MIXER), reason="example corpus missing")
need_both = pytest.mark.skipif(
    not (os.path.isdir(EX) and os.path.isdir(SA)), reason="example corpora missing"
)


def _state(po):
    """The record's colour-state array bytes and layout."""
    L = CS.layout(po)
    return po.body_raw[L["at"] : L["end"]], L


# ------------------------------------------------------------------ the palette
@need
def test_array_defaults_are_the_project_palette():
    """An array entry without a COL_ARR_<n> key is the project palette's
    colour of colour number n (in CtDraw's legacy colour order):
    every such entry of the Example pages; the first sixteen numbers are
    the classic Citect colours."""
    table = P.table_for_project(EX)
    assert [table[i][0] for i in range(16)] == list(P.STANDARD16)
    assert sorted(P.V1_ORDER) == list(range(256))
    seen = bad = 0
    for f in glob.glob(os.path.join(EX, "*.ctg")):
        for po in parse_ctg(f).page.objects:
            vb = writer.value_bag(po) if po.raw_payload is None else None
            props = dict(vb or ())
            if not props.get("COL_ARR_0"):
                continue
            L = CS.layout(po)
            for i in range(1, L.get("arr_n", 0)):
                if not props.get("COL_ARR_%d" % i):
                    seen += 1
                    bad += CS._pair_at(po.body_raw, L["arr_at"] + 8 * i) != table[i]


# ------------------------------------------------------------------ corpus
def _corpus_records(projects):
    for proj in projects:
        pal = P.table_for_project(proj)
        for f in sorted(glob.glob(os.path.join(proj, "*.ctg"))):
            for po in parse_ctg(f).page.objects:
                if po.raw_payload is not None:
                    continue
                vb = writer.value_bag(po)
                if vb and any(k.upper().startswith("COL") for k, _v in vb):
                    yield pal, po, dict(vb)


@need_both
def test_every_corpus_colour_record_survives_an_apply():
    """For every Example / ExampleSA record with a COL_ key: the page as
    shown writes nothing when nothing changed, and CtDraw's FULL apply of
    the unchanged page (every key the type's apply writes, +
    the type applies) re-serialises the identical colour-state array."""
    from ctgedit.editing import EditableDocument

    modes = {}
    old = CS._palette
    try:
        for pal, po, props in _corpus_records((EX, SA)):
            CS.use_palette(pal)
            L = CS.layout(po)
            slot = EditableDocument.fill_static_slot(po)
            b = po.body_raw
            static = (
                CS.state0_source(b, L["style_at"]) if slot is not None else CS._pair_at(b, L["state0_at"])
            )
            arr = [CS._pair_at(b, L["arr_at"] + 8 * k) for k in range(L["arr_n"])] if "arr_at" in L else None
            sta = [CS._pair_at(b, L["sta_at"] + 8 * k) for k in range(L["sta_n"])] if "sta_at" in L else None
            fc = F.read(
                props,
                static,
                static_editable=slot is not None,
                palette=pal,
                binary_array=arr,
                binary_states=sta,
            )
            assert F.apply_edits(props, fc, fc.copy()) == ([], None)
            get = F._getter(props)
            target = F.target_values(fc, get)
            if fc.mode == F.THRESHOLD:
                for i, row in enumerate(fc.thresholds):
                    s = get("COL_THR_%d" % i)
                    if s and F.Threshold.parse(s) == row:
                        target["COL_THR_%d" % i] = s
            q = copy.copy(po)
            q.body_raw = bytes(po.body_raw)
            q.bags = list(po.bags)
            writer.set_object_properties(q, [(k, v) for k, v in target.items() if get(k) != v])
            CS.rebuild(q)
            assert _state(q)[0] == b[L["at"] : L["end"]], (props, fc.mode)
            modes[fc.mode] = modes.get(fc.mode, 0) + 1
    finally:
        CS.use_palette(old)
    assert set(modes) == set(F.TYPES) and sum(modes.values()) > 12000, modes
