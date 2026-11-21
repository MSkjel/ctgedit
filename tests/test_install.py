"""CTG_SCADA_ROOT points every install lookup at a fake install."""

import ast
import os

import pytest

import hermetic
from ctgedit.infra import install

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")

#: words of the real install's whereabouts no module but infra/install may spell
_INSTALL_WORDS = ("programdata", "program files", "defaultinipath", "scada installs")
#: the modules that may import winreg: install's own lookups, and two that read other keys
#: (ActiveX classes under HKCR, the Windows theme under HKCU)
_WINREG_OK = {"infra/install.py", "io/activex.py", "ui/style/theme.py"}


def _code_strings(tree):
    """The string constants of a module that are not docstrings."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
            ):
                docs.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node


def test_only_install_module_names_the_install():
    bad = []
    for d, _sub, files in os.walk(ROOT):
        if "__pycache__" in d:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(d, f)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            if rel != "infra/install.py":
                for node in _code_strings(tree):
                    low = node.value.lower()
                    hit = next((w for w in _INSTALL_WORDS if w in low), None)
                    if hit:
                        bad.append("%s:%d names %r" % (rel, node.lineno, hit))
            if rel not in _WINREG_OK:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import) and any(a.name == "winreg" for a in node.names):
                        bad.append("%s:%d imports winreg" % (rel, node.lineno))
    assert not bad, "go through ctgedit/infra/install.py:\n" + "\n".join(bad)


# --------------------------------------------------------------------------- a fake install
def _fake_install(base, user_key=True):
    """<base>/FxRoot: Config/citect.ini, User/{Example, FxOther}, Bin (FUNC0.DBF, the
    composite template schema, Bin (x64)/zlib.dll), Docs/toc.json. Alone in its parent, as
    sibling_install_user_dirs wants."""
    root = os.path.join(str(base), "FxRoot")
    user = os.path.join(root, "User")
    for d in (
        "Config",
        "Bin",
        os.path.join("Bin", "Bin (x64)"),
        "Docs",
        os.path.join("User", "Example"),
        os.path.join("User", "FxOther"),
    ):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    ini = os.path.join(root, "Config", "citect.ini")
    with open(ini, "w", encoding="latin1", newline="") as fh:
        fh.write(
            "[CtEdit]\r\n%s\r\n[Animator]\r\nFastDisplay=1\r\n"
            "[CtExplore]\r\nDefaultTemplateStyle=FxStyle\r\n"
            % (("USER=%s" % user) if user_key else "Backup=")
        )
    for rel in (
        ("Bin", "FUNC0.DBF"),
        ("Bin", "CompositionTemplate.xsd"),
        ("Bin", "Bin (x64)", "zlib.dll"),
        ("Docs", "toc.json"),
    ):
        with open(os.path.join(root, *rel), "wb") as fh:
            fh.write(b"{}" if rel[-1].endswith(".json") else b"fx")
    return root


@pytest.fixture
def fake(tmp_path, monkeypatch):
    for k in ("CTG_SCADA_BIN", "CTG_AVEVA_HELP", "CICODESIM_INI", "CICODESIM_SCADA_BIN", "CICODESIM_FUNC0"):
        monkeypatch.delenv(k, raising=False)
    root = _fake_install(tmp_path)
    monkeypatch.setenv("CTG_SCADA_ROOT", root)
    return root


def _same(a, b):
    return a is not None and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def test_every_lookup_follows_the_fake_root(fake):
    from ctgedit.backend import compiler
    from ctgedit.format import composites
    from ctgedit.infra import paths
    from ctgedit.io import cicode_catalog, citect_config as cc, genie_help, project
    from ctgedit import help as H

    user = os.path.join(fake, "User")
    ini = os.path.join(fake, "Config", "citect.ini")
    b = os.path.join(fake, "Bin")
    with hermetic.guarding() as seen:
        assert install.mode() == "fake"
        assert _same(install.root(), fake)
        assert _same(install.active_ini(), ini) and _same(cc.active_ini_path(), ini)
        assert _same(install.user_dir(), user) and _same(cc.live_user_dir(), user)
        assert _same(cc.live_project_dir("Example"), os.path.join(user, "Example"))
        assert [os.path.normcase(p) for p in install.bin_dirs()] == [os.path.normcase(b)]
        assert paths.scada_bin_dirs() == install.bin_dirs()
        assert _same(compiler.scada_bin(), b) and _same(compiler.active_ini(), ini)
        assert _same(compiler.errs_path(ini), os.path.join(fake, "Logs", "errs"))
        assert _same(composites.schema_path(), os.path.join(b, "CompositionTemplate.xsd"))
        assert _same(install.zlib_dll(), os.path.join(b, "Bin (x64)", "zlib.dll"))
        assert _same(install.func0_dbf(), os.path.join(b, "FUNC0.DBF"))
        docs = os.path.join(fake, "Docs")
        assert _same(install.docs_dir(), docs) and _same(H.aveva_docs(), docs)
        assert H.aveva_available() and _same(cicode_catalog._help_dir(), docs)
        assert _same(genie_help._docs(), docs)
        assert H.aveva_target("1").startswith("https://")  # no such topic there
        assert install.tool_dirs() == list(reversed(install.bin_dirs()))
        ex = os.path.join(user, "Example")
        assert _same(project.default_project(), ex) and _same(project.DEFAULT_PROJECT, ex)
        assert _same(project.Project().path, ex)
        assert cc.ini_value("CtExplore", "DefaultTemplateStyle") == "FxStyle"
        assert cc.template_defaults()["style"] == "FxStyle" and cc.fast_display_enabled()
        assert cc.set_fast_display(False)  # writes the fake ini
        assert not cc.fast_display_enabled()
        assert os.path.isfile(ini + cc.BACKUP_SUFFIX)
    assert seen == [], seen


def test_the_open_folder_follows_the_fake_root(fake, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", "offscreen"))
    from ctgedit.ui import main_window_base as M

    ex = os.path.join(fake, "User", "Example")
    assert _same(M.default_open_dir(), ex) and _same(M.EXAMPLE_DIR, ex)


def test_fake_root_without_user_key_uses_its_user_folder(tmp_path, monkeypatch):
    root = _fake_install(tmp_path, user_key=False)
    monkeypatch.setenv("CTG_SCADA_ROOT", root)
    assert _same(install.user_dir(), os.path.join(root, "User"))


def test_explicit_bin_and_help_still_win(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("CTG_SCADA_BIN", str(tmp_path / "FxBin"))
    monkeypatch.setenv("CTG_AVEVA_HELP", str(tmp_path / "FxHelp"))
    assert install.bin_dirs() == [str(tmp_path / "FxBin")]
    assert install.bin_dir() == str(tmp_path / "FxBin")
    assert install.docs_dir() == str(tmp_path / "FxHelp")


def test_the_engine_is_told_the_fake_install(fake):
    from ctgedit.sim import client

    env = client.server_environment()
    assert _same(env["CICODESIM_INI"], os.path.join(fake, "Config", "citect.ini"))
    assert _same(env["CICODESIM_SCADA_BIN"], os.path.join(fake, "Bin"))
    assert _same(env["CICODESIM_FUNC0"], os.path.join(fake, "Bin", "FUNC0.DBF"))


def test_none_is_no_install(monkeypatch):
    from ctgedit.backend import compiler
    from ctgedit.format import composites
    from ctgedit.io import citect_config as cc, project
    from ctgedit import help as H
    from ctgedit.sim import client

    for k in ("CTG_SCADA_BIN", "CTG_AVEVA_HELP", "CICODESIM_INI"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CTG_SCADA_ROOT", "none")
    with hermetic.guarding() as seen:
        assert install.mode() == "none"
        assert install.root() is None and install.active_ini() is None
        assert install.user_dir() is None and cc.live_user_dir() is None
        assert install.bin_dirs() == [] and install.bin_dir() is None
        assert install.tool_dirs() == [] and install.zlib_dll() is None
        assert install.docs_dir() is None and not H.aveva_available()
        assert H.aveva_target("1").startswith("https://")
        assert composites.schema_path() is None
        assert cc.ini_value("Animator", "FastDisplay", "x") == "x"
        assert project.default_project() == "" and project.Project("").path == ""
        with pytest.raises(compiler.CompileError):
            compiler.scada_bin()
        with pytest.raises(compiler.CompileError):
            compiler.active_ini()
        with pytest.raises(cc.IniWriteError):
            cc.set_fast_display(True)
        assert client.server_environment()["CICODESIM_INI"] == ""
    assert seen == [], seen


def test_ctf_deflate_without_the_install_zlib_is_pythons(monkeypatch):
    import zlib
    from ctgedit.format import ctf

    monkeypatch.setenv("CTG_SCADA_ROOT", "none")
    monkeypatch.delenv("CTG_SCADA_BIN", raising=False)
    raw = b"fx" * 500
    assert ctf._compress(raw) == zlib.compress(raw, 5)


def test_ctf_deflate_takes_the_fake_install_zlib(fake):
    """The fake Bin's zlib.dll is the one tried (here not a DLL: Python's zlib then)."""
    import zlib
    from ctgedit.format import ctf

    raw = b"fx" * 500
    assert ctf._compress(raw) == zlib.compress(raw, 5)
    path = install.zlib_dll()
    assert path.startswith(fake) and ctf._ZLIB_LOADED.get(path) is False


def test_unset_is_the_real_lookup(monkeypatch):
    """Unset: the registry and the ProgramData glob are used (both stubbed here)."""
    monkeypatch.delenv("CTG_SCADA_ROOT", raising=False)
    monkeypatch.setattr(install, "_registry_ini", lambda: None)
    monkeypatch.setattr(install, "_INI_GLOB", os.path.join("Z:\\", "fx-none", "*.ini"))
    assert install.mode() == "real"
    assert install.user_dir() is None and install.active_ini() is None


def _real_families(tmp_path, monkeypatch):
    """A real-mode layout in tmp_path: two Plant SCADA installs and two Citect SCADA ones
    (each with a zlib.dll and a schema), the 64-bit tool folder and no CTG_SCADA_BIN."""

    def make(*parts):
        d = tmp_path.joinpath(*parts)
        (d / "Bin (x64)").mkdir(parents=True)
        (d / "Bin (x64)" / "zlib.dll").write_bytes(b"")
        (d / "CompositionTemplate.xsd").write_text("<x/>")
        return str(d)

    fx = {
        name: make(name, "Bin")
        for name in ("FxPlant SCADA 1", "FxPlant SCADA 2", "FxCitect SCADA 1", "FxCitect SCADA 2")
    }
    tools = str(tmp_path / "FxTools")
    monkeypatch.delenv("CTG_SCADA_ROOT", raising=False)
    monkeypatch.delenv("CTG_SCADA_BIN", raising=False)
    monkeypatch.setattr(
        install,
        "_BIN_GLOBS",
        (
            str(tmp_path / "FxPlant SCADA*" / "Bin"),
            str(tmp_path / "FxNone*" / "Bin"),
            str(tmp_path / "FxCitect SCADA*" / "Bin"),
        ),
    )
    monkeypatch.setattr(install, "_TOOL_DIRS", (tools,))
    return fx, tools


def test_real_lookups_keep_the_family_order(tmp_path, monkeypatch):
    """The newest install first within a family, but Plant SCADA before Citect SCADA: the
    Tools menu, the .ctF zlib, FUNC0.DBF and the composite schema all agree with bin_dir."""
    from ctgedit.format import composites

    fx, tools = _real_families(tmp_path, monkeypatch)
    best = [fx["FxPlant SCADA 2"], fx["FxPlant SCADA 1"], fx["FxCitect SCADA 2"], fx["FxCitect SCADA 1"]]
    assert install.bin_dirs_newest_first() == best
    assert install.bin_dir() == best[0]
    assert install.tool_dirs() == best + [tools]
    assert install.zlib_dll() == os.path.join(best[0], "Bin (x64)", "zlib.dll")
    assert composites.schema_path() == os.path.join(best[0], "CompositionTemplate.xsd")


def test_real_bin_override_keeps_the_install_fall_backs(tmp_path, monkeypatch):
    """CTG_SCADA_BIN (real mode) is searched first; the installs and the 64-bit tool folder
    stay the fall backs for the schema, the zlib and the Tools menu."""
    from ctgedit.format import composites

    fx, tools = _real_families(tmp_path, monkeypatch)
    env = str(tmp_path / "FxEnvBin")
    os.makedirs(env)
    monkeypatch.setenv("CTG_SCADA_BIN", env)
    dirs = install.bin_dirs_newest_first()
    assert dirs[0] == env and dirs[1] == fx["FxPlant SCADA 2"]
    assert install.tool_dirs()[0] == env and install.tool_dirs()[-1] == tools
    assert install.zlib_dll() == os.path.join(fx["FxPlant SCADA 2"], "Bin (x64)", "zlib.dll")
    assert composites.schema_path() == os.path.join(fx["FxPlant SCADA 2"], "CompositionTemplate.xsd")
