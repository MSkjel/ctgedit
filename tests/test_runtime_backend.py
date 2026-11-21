"""[CtEdit] Run is updated without touching the rest of citect.ini."""

import os


def test_set_run_project_rewrites_only_the_run_key(tmp_path, monkeypatch):
    from ctgedit.backend import runtime

    ini = tmp_path / "citect.ini"
    ini.write_bytes(
        b"[Alarm]\r\nX = 1\r\n[CtEdit]\r\nLASTDATABASE =A\r\nRun = \r\n"
        b"User =C:\\U\r\n[Page]\r\nStartup = \r\n"
    )
    monkeypatch.setattr(runtime, "active_ini", lambda: str(ini))
    monkeypatch.setattr(runtime, "root_project_for", lambda d: "FxSa")
    import ctgedit.io.citect_config as cc

    monkeypatch.setattr(cc, "live_user_dir", lambda: None)
    out = runtime.set_run_project(r"C:\P\FxSa")
    assert out == r"C:\P\FxSa"
    assert ini.read_bytes() == (
        b"[Alarm]\r\nX = 1\r\n[CtEdit]\r\nLASTDATABASE =A\r\n"
        b"Run =C:\\P\\FxSa\r\nUser =C:\\U\r\n[Page]\r\nStartup = \r\n"
    )


def test_set_run_project_adds_a_missing_key(tmp_path, monkeypatch):
    from ctgedit.backend import runtime

    ini = tmp_path / "citect.ini"
    ini.write_bytes(b"[CtEdit]\nUser =C:\\U\n")
    monkeypatch.setattr(runtime, "active_ini", lambda: str(ini))
    monkeypatch.setattr(runtime, "root_project_for", lambda d: "X")
    import ctgedit.io.citect_config as cc

    monkeypatch.setattr(cc, "live_user_dir", lambda: None)
    runtime.set_run_project(r"C:\P\X")
    assert ini.read_bytes() == b"[CtEdit]\nRun =C:\\P\\X\nUser =C:\\U\n"


def test_reap_without_pids_spares_studios_ctdraw(monkeypatch):
    """GraphicsBuilder.reap() with no pid list kills stray CtDraw32 processes
    but never the one Plant SCADA Studio owns: the COM server needs Studio,
    and killing its builder breaks Studio."""
    import subprocess
    from ctgedit.backend import graphics_builder, winui

    monkeypatch.setattr(winui, "pids_of", lambda image: [6616] if image == "CitectIDE.exe" else [])
    monkeypatch.setattr(winui, "parent_pids", lambda image: {11696: 6616, 4242: 1234})
    killed = []

    class R:
        stdout = "SUCCESS"

    def fake_run(cmd, **kw):
        killed.append(cmd)
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert graphics_builder.GraphicsBuilder.reap() == 1
    assert killed == [["taskkill", "/F", "/PID", "4242"]]


def test_parent_pids_lists_this_process():
    import sys
    from ctgedit.backend.winui import parent_pids

    me = parent_pids(os.path.basename(sys.executable))
    assert os.getpid() in me and me[os.getpid()] > 0


def _fake_runtime(monkeypatch, shown):
    """A fake runtime showing ``shown`` in its windows."""
    from ctgedit.backend import ctapi

    calls = []

    def cicode(*cmds, timeout=20.0):
        calls.append(cmds)
        out, last = [], 0
        for c in cmds:
            cur = 0
            if c.startswith("@"):
                n, c = int(c[1 : c.index(":")]), c[c.index(":") + 1 :]
                cur = n if n in shown else last
                last = cur
            if c == "WinNumber()":
                out.append(str(cur))
            elif c == "PageInfo(0)":
                # a super genie popup reports ' name' for '!name'
                name = shown.get(cur, "")
                out.append(" " + name[1:] if name.startswith("!") else name)
            else:
                out.append("0")
        return out

    monkeypatch.setattr(ctapi, "cicode", cicode)
    # the client window captions show the page TITLE, not its name
    monkeypatch.setattr(ctapi, "_captions", lambda: ["Home"])
    return calls


def test_redisplay_finds_a_titled_page_by_name(monkeypatch):
    """The runtime captions a page's window with its title (Home_HD1080's: 'Home'): the page
    is found by its name, not by the captions."""
    from ctgedit.backend import ctapi

    calls = _fake_runtime(monkeypatch, {0: "Home_HD1080"})
    assert ctapi.displayed_pages() == ["Home_HD1080"]
    assert ctapi.redisplay("home_hd1080") is True
    assert calls[-1] == ('@0:PageDisplay("Home_HD1080")',)
    assert ctapi.redisplay("WTP_INTAKE_HD1080") is False


def test_redisplay_uses_the_window_that_shows_the_page(monkeypatch):
    from ctgedit.backend import ctapi

    calls = _fake_runtime(monkeypatch, {0: "Home_HD1080", 3: "Popup_HD1080", 1: "!wp23pop"})
    assert sorted(ctapi.displayed_pages()) == ["!wp23pop", "Home_HD1080", "Popup_HD1080"]
    assert ctapi.redisplay("Popup_HD1080") is True
    assert calls[-1] == ('@3:PageDisplay("Popup_HD1080")',)
    assert ctapi.window_pages() == {0: "Home_HD1080", 1: "!wp23pop", 3: "Popup_HD1080"}


def test_redisplay_without_a_client_window_asks_nothing(monkeypatch):
    from ctgedit.backend import ctapi

    calls = _fake_runtime(monkeypatch, {})
    monkeypatch.setattr(ctapi, "_captions", lambda: [])
    assert ctapi.redisplay("Home_HD1080") is False
    assert ctapi.displayed_pages() == []
    assert calls == []
