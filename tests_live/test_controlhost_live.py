"""The real control host (ctgctl.exe, built from ctgedit/native/control_host.cs) with the
controls Plant SCADA installs: each takes a picture of itself from ctgedit's seed state."""

import os

import pytest

from ctgedit.backend import controlhost as H
from ctgedit.format import factory, olecontrol
from ctgedit.io import activex as AX
from ctgedit.io.pgregister import ole_clsid as _ole_clsid


def _seed(kind):
    po = factory.create_object(kind, (0, 0, 320, 200), handle=7)
    return _ole_clsid(po.body_raw), olecontrol.storage(po)


@pytest.mark.parametrize("kind", ["activex", "process_analyst", "scheduler"])
def test_an_installed_control_draws_itself(kind, monkeypatch):
    if not H.available():
        pytest.skip("no .NET Framework 4 compiler")
    clsid, state = _seed(kind)
    if AX.control_info(clsid) is None:
        pytest.skip("%s is not registered on this computer" % kind)
    monkeypatch.delenv("CTG_NO_CONTROL_HOST", raising=False)
    png = H.take_picture(clsid, state, 320, 200)
    assert png and os.path.isfile(png), kind
    from PySide6 import QtGui

    img = QtGui.QImage(png)
    assert (img.width(), img.height()) == (320, 200)
    colours = {img.pixel(x, y) for x in range(0, 320, 7) for y in range(0, 200, 7)}
    assert len(colours) > 3, "%s: a plain picture" % kind


def _edit_with(kind, monkeypatch, type_text):
    """Run the real edit window of ``kind``'s seed control: type ``type_text`` (None: nothing)
    into the first text box of its property pages, press OK; (exit code, saved bytes)."""
    import subprocess
    import tempfile
    import time

    win32gui = pytest.importorskip("win32gui")
    import win32con
    import win32process

    if not H.available():
        pytest.skip("no .NET Framework 4 compiler")
    clsid, state = _seed(kind)
    if AX.control_info(clsid) is None:
        pytest.skip("%s is not registered on this computer" % kind)
    monkeypatch.delenv("CTG_NO_CONTROL_HOST", raising=False)
    d = tempfile.mkdtemp()
    stg, new = os.path.join(d, "s.stg"), os.path.join(d, "n.stg")
    with open(stg, "wb") as f:
        f.write(state)
    p = subprocess.Popen([H.host_exe(), "edit", clsid, stg, new, "320", "200"], env=H._environment())

    def kids(h):
        out = []
        win32gui.EnumChildWindows(h, lambda c, _: out.append(c) or True, None)
        return out

    ok, dialog = None, None
    try:
        for _ in range(300):
            time.sleep(0.1)
            tops = []

            def mine(h, _):
                if win32process.GetWindowThreadProcessId(h)[1] == p.pid:
                    tops.append(h)
                return True

            win32gui.EnumWindows(mine, None)
            for h in tops:
                if win32gui.GetWindowText(h) == "Control Properties":
                    oks = [c for c in kids(h) if win32gui.GetWindowText(c) == "OK"]
                    if oks:
                        dialog, ok = h, oks[0]
            if ok:
                break
        assert ok, "the property pages did not open"
        time.sleep(1.0)
        if type_text is not None:
            box = next(c for c in kids(dialog) if "textbox" in win32gui.GetClassName(c).lower()
                       and win32gui.IsWindowVisible(c) and win32gui.IsWindowEnabled(c))  # fmt: skip
            win32gui.SendMessage(box, win32con.WM_SETTEXT, 0, type_text)
            win32gui.SendMessage(win32gui.GetParent(box), win32con.WM_COMMAND,
                                 (win32con.EN_CHANGE << 16) | win32gui.GetDlgCtrlID(box), box)  # fmt: skip
        win32gui.PostMessage(ok, win32con.BM_CLICK, 0, 0)
        code = p.wait(30)
    finally:
        if p.poll() is None:
            p.kill()
    saved = b""
    if os.path.isfile(new):
        with open(new, "rb") as f:
            saved = f.read()
    return code, saved, clsid


def test_ok_without_a_change_leaves_the_control_as_it_was(monkeypatch):
    code, saved, _clsid = _edit_with("activex", monkeypatch, None)
    assert code == H.CANCELLED and saved == b""


def test_a_change_in_the_property_pages_is_saved_and_reloads(monkeypatch):
    code, saved, clsid = _edit_with("activex", monkeypatch, "Typed by the test")
    assert code == H.DONE and saved[:8] == olecontrol.CFB_SIGNATURE
    po = factory.create_object("activex", (0, 0, 320, 200), handle=7)
    assert olecontrol.replace_storage(po, saved)
    assert _ole_clsid(po.body_raw) == clsid  # the control's class kept
    assert H.take_picture(clsid, saved, 320, 200)  # it loads and draws from the new state
