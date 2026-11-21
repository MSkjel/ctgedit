"""Update Pages: compose nested genies again where they differ."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_element_nested_update import _lib, _stale_but_current  # noqa: E402


def _inner(lib, name):
    """(x, y) of the genie root nested two levels down in ``name`` (n1's copy of n0)."""
    from ctgedit.editing import ElementDocument

    objs = ElementDocument.open_element(lib, name).page.objects
    k = [i for i, o in enumerate(objs) if o.kind.name == "GENIE"][1]
    return objs[k].geometry.x, objs[k].geometry.y


def test_the_chain_composes_a_host_whose_stamps_say_current(fx_install):
    from ctgedit.io import genieusage as GU

    lib = _lib(fx_install)
    _stale_but_current(fx_install)
    assert _inner(lib, "n2") == (10, 70)  # (the copy as kept)
    dirs = [fx_install.project("FxLib")]
    plain = GU.chain_plan(dirs)
    assert "fxnest.n2" not in [u.ref.label for u in plain.elements]
    seen = []
    plan = GU.chain_plan(
        dirs, compose_all=True, compose_progress=lambda i, n, name: seen.append(name) or True
    )
    labels = [u.ref.label for u in plan.elements]
    assert "fxnest.n2" in labels and seen
    n2 = next(u for u in plan.elements if u.ref.label == "fxnest.n2")
    assert n2.compose_all and n2.needs_update
    # the genies that place it wait for it, the deepest first
    assert labels.index("fxnest.n2") < labels.index("fxnest.n3") < labels.index("fxnest.n4")
    rep = GU.run_update(GU.UpdatePlan(plan.elements, [], plan.skipped))
    assert "fxnest.n2" in rep.elements_saved, (rep.elements_saved, rep.skipped, rep.failed)
    assert _inner(lib, "n2") == (17, 75)  # (composed again)
    # once current, the check finds nothing more for it
    again = GU.chain_plan(dirs, compose_all=True)
    assert "fxnest.n2" not in [u.ref.label for u in again.elements]


def test_a_stop_while_comparing_plans_nothing(fx_install):
    from ctgedit.io import genieusage as GU

    plan = GU.chain_plan(
        [fx_install.project("FxLib")], compose_all=True, compose_progress=lambda i, n, name: False
    )
    assert plan.stopped and not plan.elements


def test_the_update_pages_box_is_under_the_chain_box_and_remembered(fx_install):
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog
    from ctgedit.ui.prefs.settings import app_settings

    dlg = UpdatePagesDialog(fx_install.project("FxMain"), None)
    try:
        dlg._gen.setChecked(True)
        dlg._chain.setChecked(False)
        assert not dlg._chain_all.isEnabled()
        dlg._chain.setChecked(True)
        assert dlg._chain_all.isEnabled() and not dlg._chain_all.isChecked()
        dlg._chain_all.setChecked(True)
        assert dlg.options()["chain_compose_all"] is True
        # (the chain works for templates too: with Templates still ticked it stays on)
        dlg._tpl.setChecked(True)
        dlg._gen.setChecked(False)
        assert dlg._chain_all.isEnabled() and dlg.options()["chain_compose_all"]
        dlg._tpl.setChecked(False)
        assert not dlg._chain_all.isEnabled() and not dlg.options()["chain_compose_all"]
        dlg._gen.setChecked(True)
        dlg.accept()
    finally:
        dlg.deleteLater()
    assert app_settings().value("updatepages/chain_compose_all") in (True, "true")
