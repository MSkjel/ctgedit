"""The hermetic guard catches what it should."""

import glob
import os
import subprocess
import sys
import textwrap

import pytest

import hermetic

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2"
BIN = r"C:\Program Files (x86)\AVEVA Plant SCADA\Bin"


def test_open_of_the_install_is_refused():
    with hermetic.guarding() as seen:
        with pytest.raises(hermetic.HermeticViolation):
            open(os.path.join(R2, "Config", "citect.ini"), "rb")
        with pytest.raises(hermetic.HermeticViolation):
            open(BIN.replace("\\", "/") + "/fx.dll", "rb")  # forward slashes too
    assert len(seen) == 2 and "open of" in seen[0]


def test_listings_and_globs_of_the_install_are_refused():
    with hermetic.guarding() as seen:
        for call in (
            lambda: os.listdir(R2),
            lambda: list(os.scandir(BIN)),
            lambda: glob.glob(r"C:\ProgramData\AVEVA Plant SCADA*\Config\*.ini"),
        ):
            with pytest.raises(hermetic.HermeticViolation):
                call()
    assert [s.split(" ")[0] for s in seen] == ["os.listdir", "os.scandir", "glob.glob"]


def test_a_swallowed_refusal_is_still_noted():
    with hermetic.guarding() as seen:
        try:
            os.listdir(R2)
        except Exception:  # noqa: BLE001 -- what a careless caller does
            pass
    assert seen and R2 in seen[0]


def test_com_imports_are_refused():
    with hermetic.guarding() as seen:
        for name in ("win32com.client", "pythoncom", "comtypes"):
            with pytest.raises(ImportError):
                __import__(name)
    assert [s for s in seen if s.startswith("import of")] == [
        "import of win32com",
        "import of pythoncom",
        "import of comtypes",
    ]  # (the package first)


def test_citect_programs_are_not_started():
    with hermetic.guarding() as seen:
        for args in (
            ["CtDraw32.exe"],
            [os.path.join(BIN, "CtCmp32.exe"), "/I", "x"],
            ["cmd", "/c", "start", "CitectIDE.exe"],
        ):
            with pytest.raises(hermetic.HermeticViolation):
                subprocess.Popen(args)
    assert len(seen) == 3, seen
    assert "CtDraw32" in seen[0] and "CtCmp32" in seen[1] and "CitectIDE" in seen[2]


def test_citect_registry_keys_are_refused():
    winreg = pytest.importorskip("winreg")
    with hermetic.guarding() as seen:
        with pytest.raises(hermetic.HermeticViolation):
            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Citect")
    assert seen and "Citect" in seen[0]


def test_the_network_is_refused_but_loopback_goes_through():
    import socket

    with hermetic.guarding() as seen:
        with pytest.raises(hermetic.HermeticViolation):
            socket.getaddrinfo("api.github.com", 443)
        with socket.socket() as s, pytest.raises(hermetic.HermeticViolation):
            s.connect(("192.0.2.1", 443))
        a, b = socket.socketpair()  # loopback
        a.close()
        b.close()
    assert len(seen) == 2 and "api.github.com" in seen[0]


def test_the_rest_goes_through(tmp_path):
    p = tmp_path / "fx.txt"
    with hermetic.guarding() as seen:
        p.write_text("fx")
        assert os.listdir(tmp_path) == ["fx.txt"]
        assert glob.glob(str(tmp_path / "*.txt"))
        r = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True)
        assert r.returncode == 0
    assert seen == []


def test_the_rules():
    assert hermetic.blocked_path(r"c:\programdata\aveva\product documentation")
    assert hermetic.blocked_path(b"C:/Program Files/AVEVA Plant SCADA/Bin")
    assert not hermetic.blocked_path(r"C:\ProgramData\Fx")
    assert not hermetic.blocked_path(3)
    assert hermetic.blocked_program(None, '"C:\\Fx\\CtDraw32.exe" /x')
    assert not hermetic.blocked_program(None, "taskkill /F /PID 12")
    assert not hermetic.blocked_program(None, ["tasklist", "/FI", "IMAGENAME eq CtDraw32.exe"])


def test_a_hermetic_session_fails_a_test_that_reaches_out(tmp_path):
    """The plugin end to end, in a pytest of its own (CTG_HERMETIC=1, -p hermetic): the
    test that swallowed its refusal fails, the others pass, the install is 'none'."""
    t = tmp_path / "test_fx_reach.py"
    t.write_text(
        textwrap.dedent(
            """
        import os

        def test_reaches_out():
            try:
                os.listdir(r"%s")
            except Exception:
                pass

        def test_stays_home(tmp_path):
            (tmp_path / "fx").write_text("fx")

        def test_no_install():
            assert os.environ["CTG_SCADA_ROOT"] == "none"
            assert "CTG_SCADA_BIN" not in os.environ
    """
            % R2
        )
    )
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(
        os.environ,
        CTG_HERMETIC="1",
        CTG_SCADA_BIN=r"C:\Fx\Bin",
        PYTHONPATH=os.pathsep.join([here, os.path.dirname(here)]),
    )
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "hermetic", "-p", "no:cacheprovider", str(t)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "1 failed, 2 passed" in out, out
    assert "it reached outside its fake install" in out and "os.listdir" in out, out


def _run_plugin(tmp_path, files):
    """Run a pytest of its own on ``files`` (name -> source) with the guard on."""
    for name, src in files.items():
        (tmp_path / name).write_text(textwrap.dedent(src))
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, CTG_HERMETIC="1", PYTHONPATH=os.pathsep.join([here, os.path.dirname(here)]))
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "hermetic",
            "-p",
            "no:cacheprovider",
            "-rA",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )
    return r.returncode, r.stdout + r.stderr


def test_a_failing_or_skipped_test_is_reported_once(tmp_path):
    """A test that reached out and then failed shows its notes in its own report, with no
    second error at teardown; a skip that reached out fails, named by its phase."""
    code, out = _run_plugin(
        tmp_path,
        {
            "test_fx_once.py": """
        import os
        import pytest

        def _reach():
            try:
                os.listdir(r"%s")
            except Exception:
                pass

        def test_fails_too():
            _reach()
            raise ValueError("fx")

        def test_skips():
            _reach()
            pytest.skip("fx")
    """
            % R2
        },
    )
    assert code == 1, out
    assert "2 failed" in out and "error" not in out.lower().replace("valueerror", ""), out
    assert "hermetic (call)" in out and "it also reached outside" in out, out
    assert "hermetic test (call)" in out and "teardown" not in out, out


def test_reaches_between_tests_fail_the_run(tmp_path):
    """A refusal noted after a test's teardown (a background thread) is not dropped: the
    run lists it and fails, though every test passed."""
    code, out = _run_plugin(
        tmp_path,
        {
            "conftest.py": """
            import os

            def pytest_runtest_logfinish(nodeid, location):
                try:
                    os.listdir(os.path.join(r"%s", nodeid.rsplit("::", 1)[-1]))
                except Exception:
                    pass
        """
            % R2,
            "test_fx_between.py": """
            def test_first():
                pass

            def test_last():
                pass
        """,
        },
    )
    assert code == 1, out
    assert "2 passed" in out, out
    assert "between tests, a background thread reached outside" in out, out
    assert "test_first" in out and "test_last" in out, out


def test_the_forced_environment_is_the_fixtures():
    """No library folder of the machine; the fixtures' 96 dpi and code page 1252."""
    env = {"CTG_LIB_PATH": r"C:\Fx\Libs", "CTG_TEXT_DPI": "144", "CTG_CODEPAGE": "1250"}
    hermetic.force_environment(env)
    assert "CTG_LIB_PATH" not in env
    assert env["CTG_TEXT_DPI"] == "96" and env["CTG_CODEPAGE"] == "1252"
    assert os.environ["CTG_TEXT_DPI"] and os.environ["CTG_CODEPAGE"]  # (tests/conftest.py)


def test_no_control_is_registered_without_the_real_install(monkeypatch):
    """With a fake install or none, io/activex reads no HKCR class: the same answer on
    every machine."""
    from ctgedit.io import activex as AX

    seen = []
    monkeypatch.setattr(AX, "_reg", lambda path, value="": seen.append(path) or "FxControl")
    AX._control_info.cache_clear()
    AX._control_members.cache_clear()
    try:
        for root in ("none", r"C:\FxInstall"):
            monkeypatch.setenv("CTG_SCADA_ROOT", root)
            assert AX.control_info("{6A3F1C2E-5B7D-4E8A-9C0F-1D2E3F405162}") is None
            assert AX.control_members("{6A3F1C2E-5B7D-4E8A-9C0F-1D2E3F405162}") is None
        assert seen == []
        monkeypatch.delenv("CTG_SCADA_ROOT")
        info = AX.control_info("{6A3F1C2E-5B7D-4E8A-9C0F-1D2E3F405162}")
        assert info is not None and info.name == "FxControl" and seen
    finally:
        AX._control_info.cache_clear()
        AX._control_members.cache_clear()
