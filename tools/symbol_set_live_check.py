"""Check symbol set edits in the live Graphics Builder (Studio running, no builder window open)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.io.citect_config import live_project_dir  # noqa: E402

PAGE = "!Alarmsim"


def build_edits():
    """(edited objects, link table raw, link count, {handle: label})."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import symbolset as S

    pdir = live_project_dir("Example")
    tmp = tempfile.mkdtemp(prefix="ctg_symset_")
    try:
        path = os.path.join(tmp, PAGE + ".ctg")
        shutil.copy2(os.path.join(pdir, PAGE + ".ctg"), path)
        os.environ["CTG_LIB_PATH"] = pdir
        doc = EditableDocument.open(path)
        doc.set_state_symbol(56, 1, ("lights", "light_1_green"))
        doc.clear_state(59, 0)
        info = doc.symbol_set_info(62)
        grey, red = info["states"][0]["symbol"], info["states"][1]["symbol"]
        doc.apply_symbol_set(
            62, S.MULTI_STATE, ["BIT_3", "BIT_4"], [grey, red, ("lights", "light_1_green"), red]
        )
        new = doc.create_symbol_set((200, 160), exprs=["BIT_1"])
        objs = doc.parsed.page.objects
        labels = {}
        for root, what in ((56, "swap ON"), (59, "clear OFF"), (62, "multi-state"), (new, "new set")):
            for k in doc.subtree(root):
                labels[G.get_handle(objs[k])] = "%s [%d]" % (what, k)
        pb = doc.parsed.page
        return list(objs), pb.link_table_raw, pb.link_count, labels
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    from tools.engine_roundtrip import field_diff, roundtrip, save_capture

    objs, raw, count, labels = build_edits()

    def mutate(p):
        p.page.objects[:] = objs
        p.page.link_table_raw, p.page.link_count = raw, count
        return list(labels)

    cap = roundtrip(mutate, element=PAGE)
    path = save_capture("symbol_sets", cap, labels)
    fixed = 0
    for h, (ours, eng) in sorted(cap["records"].items()):
        d = field_diff(ours, eng) if ours else {"missing_ours": True}
        size_only = (
            set(d) <= {"body_after_geom"}
            and d
            and all(len(op["ours"]) == len(op["eng"]) == 2 for op in d["body_after_geom"]["ops"])
        )
        ok = not d or size_only
        fixed += ok
        print(
            "%5d %-22s %s"
            % (
                h,
                labels.get(h, ""),
                "FIXED" if not d else ("size word only" if size_only else json.dumps(d, default=str)[:300]),
            )
        )
    print(
        "engine_rewrote=%s opened=%s saved=%s fixed %d/%d -> %s"
        % (cap["engine_rewrote"], cap["opened"], cap["saved"], fixed, len(cap["records"]), path)
    )
    return 0 if cap["engine_rewrote"] and fixed == len(cap["records"]) else 1


if __name__ == "__main__":
    sys.exit(main())
