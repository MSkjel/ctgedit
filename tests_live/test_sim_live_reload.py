"""Live reload on the real simulator."""

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_sim_engine_source import wait_for  # noqa: E402


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _pump():
    from PySide6 import QtWidgets

    QtWidgets.QApplication.processEvents()


def _write(path, text):
    with open(path, "w", encoding="cp1252", newline="") as f:
        f.write(text)


# -- the real simulator (optional): a .ci function's new return value shows on the page -----
from test_sim_pages_live import env as live_env, install  # noqa: E402,F401 -- fixtures


def _complete_chain(user):
    """The copied install with every project the copies include (Tab_Style_Include,
    Library_Controls ...): the simulator compiles the unit from source only when every
    included project is there (else a reload finds its file 'not a file of the unit')."""
    import shutil
    import test_sim_pages_live as L
    from ctgedit.format import projects as P
    from test_sim_ctsim_live import write_dbf

    have = {n.lower() for n in os.listdir(user) if os.path.isdir(os.path.join(user, n))}
    todo = sorted(have)
    added = False
    while todo:
        for inc in P.included_project_names(os.path.join(user, todo.pop())):
            src = os.path.join(L.R2_USER, inc)
            if inc.lower() in have or not os.path.isdir(src):
                continue
            os.makedirs(os.path.join(user, inc))
            for f in os.listdir(src):
                if L._wanted(inc, f) and os.path.isfile(os.path.join(src, f)):
                    shutil.copyfile(os.path.join(src, f), os.path.join(user, inc, f))
            have.add(inc.lower())
            todo.append(inc)
            added = True
    if added:
        rows = [
            {"NAME": n, "PATH": os.path.join(user, n)}
            for n in sorted(os.listdir(user))
            if os.path.isdir(os.path.join(user, n))
        ]
        write_dbf(os.path.join(user, "MASTER.DBF"), [("NAME", 64), ("PATH", 200)], rows)


def test_live_a_changed_ci_function_shows_on_the_running_page(live_env):
    """ctsim.exe on a COPY of R2 Example (never the live project): the page shows a Cicode
    function's value; the .ci file saved with another return value is reloaded by the
    Simulate window and the running page shows the new value, without a restart."""
    import test_sim_pages_live as L

    install, hub = live_env
    _complete_chain(install["user"])
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    probe = os.path.join(install["user"], "Example", "LiveReloadProbe.ci")
    _write(probe, "INT FUNCTION LiveReloadProbe()\r\n\tRETURN 111;\r\nEND\r\n")
    path = L.page_path(install, "Example", "Mixer")
    doc = EditableDocument.open(path)
    assert doc.set_property(L._an_index(doc, 542), "TXT_STR", "LiveReloadProbe()")
    win = SimulateWindow()
    try:
        sim = EngineSimulation(hub=hub, location=install["loc"])
        sp = win.open_page(path, sim=sim, doc=doc, parsed=doc.parsed)
        win.show()

        def text():
            win.tick()
            return str(getattr(sp.scene.sim_states().get(542), "text", "") or "")

        assert wait_for(
            lambda: sim.page_active() or (sim.status().level == "warn" and not sim.status().busy),
            timeout=90,
            pump=_pump,
        )
        if not sim.page_active():
            pytest.fail(sim.status().text)
        assert wait_for(lambda: "111" in text(), timeout=30, pump=_pump), text()
        unit = [os.path.normcase(d) for _n, d in sim.session.unit]
        assert os.path.normcase(os.path.dirname(probe)) in unit, unit
        time.sleep(0.1)
        _write(probe, "INT FUNCTION LiveReloadProbe()\r\n\tRETURN 222;\r\nEND\r\n")
        assert wait_for(lambda: "222" in text(), timeout=90, pump=_pump), (
            text(),
            [e.text for e in sp.log][-10:],
        )
        assert sim.page_active()
    finally:
        win.close()
        win.deleteLater()
