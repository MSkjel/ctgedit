"""ActiveX controls over the real corpus."""

import os

import pytest


def test_control_property_list_rebuild_is_a_no_op_on_the_corpus():
    """Every corpus control whose list parses already lists exactly its keys.
    The pages holding controls come from the corpus index (tools/corpus_index.py;
    skipped when it has not been built)."""
    import sqlite3
    from ctgedit.format import olecontrol
    from ctgedit.format.page import parse_ctg

    db = os.environ.get("CTG_CORPUS_DB") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "_corpus", "corpus.sqlite"
    )
    if not os.path.exists(db):
        pytest.skip("corpus index not built")
    con = sqlite3.connect(db)
    files = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT f.path FROM records r JOIN files f ON f.id = r.file_id "
            "WHERE r.tc = 13 AND f.ext = '.ctg'"
        )
    ]
    con.close()
    seen = 0
    for f in files:
        if not os.path.exists(f):
            continue
        try:
            objs = parse_ctg(f).page.objects
        except Exception:  # noqa: BLE001
            continue
        for po in objs:
            if po.type_code == 0x0D and olecontrol.find_list(po.body_raw) is not None:
                before = po.body_raw
                assert olecontrol.rebuild_list(po) is False, f
                assert po.body_raw == before
                seen += 1
    if not seen:
        pytest.skip("no ActiveX controls in the corpus")
    assert seen >= 40
