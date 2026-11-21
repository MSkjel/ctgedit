"""The Cicode engine doesn't live in ctgedit."""

import ast
import os
import re
from typing import Iterator, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THIS = os.path.abspath(__file__)

#: modules of a Python Cicode engine and anything below them
FORBIDDEN = (
    "ctgedit.cicode",
    "ctgedit.sim.world",
    "ctgedit.sim.builtins",
    "ctgedit.sim.animator",
    "ctgedit.sim.server",
)
#: the engine checkout is never imported by ctgedit (it is .NET, reached over ctsim's protocol),
#: nor the Python reference's package in it (reference/python/cicode_ref)
FORBIDDEN_TOP = ("external", "cicode_sim", "cicodesim", "cicode_ref")
#: package folders that must not exist
FORBIDDEN_DIRS = ("ctgedit/cicode", "ctgedit/sim/world", "ctgedit/sim/builtins")
#: the folders whose Python is checked
SCANNED = ("ctgedit", "tests", "tests_corpus", "tests_live", "tools", "packaging")

_NAME = re.compile(r"\b(ctgedit\.(?:cicode|sim\.(?:world|builtins|animator|server))|cicode_ref)\b")


def _forbidden(module: str) -> bool:
    if not module:
        return False
    if module.split(".")[0] in FORBIDDEN_TOP:
        return True
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN)


def _package_of(path: str) -> Optional[str]:
    """The dotted package a file under ctgedit/ belongs to (None outside the package)."""
    try:
        rel = os.path.relpath(os.path.abspath(path), REPO).replace(os.sep, "/")
    except ValueError:  # (another drive than the checkout)
        return None
    if not rel.startswith("ctgedit/"):
        return None
    # a module's package is its folder; an __init__.py's package is itself (the same folder)
    return ".".join(rel[:-3].split("/")[:-1])


def imported_modules(source: str, path: str) -> Iterator[Tuple[int, str]]:
    """(line, absolute module) for every import in the source, relative imports resolved
    against the file's package; ``from a import b`` also yields ``a.b`` (b may be a module)."""
    tree = ast.parse(source, filename=path)
    package = _package_of(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                if package is None:
                    continue
                parts = package.split(".")
                if node.level - 1 > len(parts):
                    continue
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([base] if base else []))
            if base:
                yield node.lineno, base
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, (base + "." if base else "") + alias.name
        elif isinstance(node, ast.Call):
            # importlib.import_module("...") / __import__("...")
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield node.lineno, arg.value


def check_file(path: str) -> List[Tuple[int, str]]:
    with open(path, "rb") as fh:
        source = fh.read().decode("utf-8-sig", "replace")
    out = [(line, "imports " + mod) for line, mod in imported_modules(source, path) if _forbidden(mod)]
    # a module named in a string (a lazy import by name, a spec hidden import)
    for n, text in enumerate(source.splitlines(), 1):
        m = _NAME.search(text)
        if m:
            out.append((n, "names " + m.group(1)))
    return out


def _python_files() -> Iterator[str]:
    for top in SCANNED:
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, top)):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "_soak", "_ctdraw")]
            for name in filenames:
                if name.endswith((".py", ".spec")):
                    path = os.path.join(dirpath, name)
                    if os.path.abspath(path) != THIS:
                        yield path


def test_nothing_imports_the_python_engine():
    problems = []
    for path in _python_files():
        rel = os.path.relpath(path, REPO)
        for line, what in check_file(path):
            problems.append("%s:%d: %s" % (rel, line, what))
    assert not problems, "\n".join(problems)


def _has_sources(folder):
    """A folder left with only a __pycache__ (an old checkout's) is not a package."""
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        if any(n.endswith(".py") for n in filenames):
            return True
    return False


def test_the_engine_packages_are_gone():
    back = [d for d in FORBIDDEN_DIRS if _has_sources(os.path.join(REPO, *d.split("/")))]
    assert not back, "engine packages back in ctgedit: %s" % back


def test_the_wire_format_stays():
    from ctgedit.sim.state import ObjectState, PageState

    st = ObjectState(an=7, fill_colour=0x00FF00, symbol_state=2)
    assert not st.is_default() and ObjectState(an=7).is_default()
    assert PageState(window=0, page="Mixer").objects == {}


def test_the_check_finds_what_it_should(tmp_path):
    # a file inside the package: relative imports resolve against ctgedit.render
    src = (
        "from ..cicode import values\n"
        "from ..sim import world\n"
        "from ..sim.builtins.maths import Sin\n"
        "import ctgedit.cicode.vm as vm\n"
        "from ctgedit.sim import state, builtins\n"
        "import importlib\n"
        "m = importlib.import_module('ctgedit.sim.world.tags')\n"
        "HIDDEN = ['ctgedit.cicode.program']\n"
        "from ..sim.state import ObjectState\n"
        "from . import scene\n"
        "import external.cicode_sim\n"
        "from cicode_ref.cicode import vm\n"
        "REF = 'cicode_ref.sim.world'\n"
    )
    path = os.path.join(REPO, "ctgedit", "render", "_boundary_probe.py")
    found = []
    for line, mod in imported_modules(src, path):
        if _forbidden(mod):
            found.append((line, mod))
    assert sorted(found) == [
        (1, "ctgedit.cicode"),
        (1, "ctgedit.cicode.values"),
        (2, "ctgedit.sim.world"),
        (3, "ctgedit.sim.builtins.maths"),
        (3, "ctgedit.sim.builtins.maths.Sin"),
        (4, "ctgedit.cicode.vm"),
        (5, "ctgedit.sim.builtins"),
        (7, "ctgedit.sim.world.tags"),
        (11, "external.cicode_sim"),
        (12, "cicode_ref.cicode"),
        (12, "cicode_ref.cicode.vm"),
    ]
    probe = tmp_path / "probe.py"
    probe.write_text(src, encoding="utf-8")
    named = [line for line, what in check_file(str(probe)) if what.startswith("names ")]
    assert named == [4, 7, 8, 12, 13]
