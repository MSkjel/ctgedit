"""Genie forms over the installed projects and help."""

import os

import pytest

import corpus

R2 = corpus.R2_USER


@pytest.mark.skipif(not os.path.isdir(R2), reason="R2 install not present")
def test_shipped_forms_parse_like_ctdraw():
    """Shipped genie forms parse like CtDraw."""
    import glob
    from ctgedit.format import genieform as GF

    bad = {}
    where = {}
    n = 0
    for p in sorted(set(glob.glob(os.path.join(R2, "*", "*.frm")))):
        for f in GF.load_form_file(p):
            n += 1
            if not f.valid:
                name = os.path.basename(p).lower()
                bad.setdefault(name, set()).add(f.name)
                where.setdefault(name, set()).add(os.path.basename(os.path.dirname(p)))
    assert n > 300
    user = set(corpus.user_projects())
    shipped = {name: sorted(projects - user) for name, projects in where.items() if projects - user}
    assert not shipped, shipped
    known = corpus.role_value("invalid_forms")
    if isinstance(known, dict):
        known = {k.lower(): v for k, v in known.items()}
        assert set(bad) <= set(known), bad
        for name, genies in known.items():
            if name in bad:
                assert set(genies or ()) <= bad[name], (name, bad[name])


# ------------------------------------------------------------------ reference
@pytest.mark.skipif(
    not os.path.isdir(r"C:\ProgramData\AVEVA\Product Documentation\Plant SCADA\content\en"),
    reason="AVEVA help not installed",
)
def test_system_genies_link_to_their_reference():
    from ctgedit.io import genie_help as GH

    assert GH.topic_for("sa_workspace", "pane") == "1206806"
    assert GH.topic_for("sa_controls", "treeview") == "1206793"
    assert GH.topic_for("sa_common", "meo_16") == "1206385"  # 'meo_<n>'
    assert GH.topic_for("fx_values", "fx_genie") is None  # (not an AVEVA library)
    desc = GH.field_descriptions("1206806")
    assert "default pane" in desc["isdefaultpane"].lower()
    assert GH.norm_label("Is Default Pane?") == "isdefaultpane"
