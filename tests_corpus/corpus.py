"""Corpus pins: report 'input changed' instead of failing when a live page changed."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PINS_FILE = os.path.join(HERE, "corpus_pins.json")
#: this machine's roles (git-ignored) and their documentation (committed)
LOCAL_FILE = os.path.join(HERE, "local.json")
EXAMPLE_FILE = os.path.join(HERE, "local.example.json")
R2_USER = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
#: the projects AVEVA ships (lower case): every other project folder is the user's own
SHIPPED_PROJECTS = frozenset(
    (
        "example",
        "examplesa",
        "include",
        "system",
        "industrialgraphics_include",
        "library_controls",
        "library_equipment",
        "sa_controls",
        "sa_include",
        "sa_library",
        "sxw_style_include",
        "tab_style_include",
    )
)
#: the role name endings that name install paths (relative to the User folder)
PATH_SUFFIXES = ("_project", "_page", "_library", "_pages")
#: a file a role value names: 'Project/File.ctg' (either slash)
_ROLE_FILE = re.compile(r"^[^\\/\n]+[\\/][^\\/\n]+\.(?:ctg|ctm|ctl|ctt)$", re.I)
#: the folders whose test files name the corpus files they read (tools/pin_corpus.py)
TEST_DIRS = tuple(os.path.join(REPO, d) for d in ("tests", "tests_corpus", "tests_live"))
#: a quoted file name, also after a folder ('Example\\Genies.ctg', "Proj/Lib.ctm")
_NAME = re.compile(r"""["'](?:[^"'\n]*[\\/])?([\w!\-. ]+\.(?:ctg|ctm|ctl|ctt))["']""", re.I)

_pins: Dict[str, str] = {}
_sha: Dict[str, tuple] = {}
_roles: Dict[str, Any] = {}
_roles_key: list = [None]


# --------------------------------------------------------------------------- the local roles
def roles() -> Dict[str, Any]:
    """This machine's roles (tests_corpus/local.json): {} when the file is missing or is not
    a JSON object. Keys that start with '_' are comments. Read again when the file changes."""
    try:
        st = os.stat(LOCAL_FILE)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key != _roles_key[0]:
        _roles.clear()
        _roles_key[0] = key
        data = None
        if key is not None:
            try:
                with open(LOCAL_FILE, encoding="utf-8") as fh:
                    data = json.load(fh)
            except OSError, ValueError:
                data = None
        if isinstance(data, dict):
            _roles.update((k, v) for k, v in data.items() if not k.startswith("_"))
    return _roles


def not_configured(name: str) -> str:
    """The skip reason of a role that is not set."""
    return "not configured: set %s in tests_corpus/local.json (see local.example.json)" % name


def role_value(name: str, default=None):
    """The role's value, or ``default`` when it is not set (null, '', [] and {} are not set).
    No skip: for tools and for module level code that runs at collection."""
    v = roles().get(name)
    return default if v in (None, "", [], {}) else v


def install_path(rel: str) -> str:
    """A role's 'Project/File.ctg' (or 'Project') as a path in the install's User folder."""
    return os.path.join(R2_USER, *re.split(r"[\\/]+", rel.strip("\\/")))


def is_path_role(name: str) -> bool:
    """A role whose value is an install path ('_project', '_page', '_library', '_pages')."""
    return name.endswith(PATH_SUFFIXES)


def role_path_or_none(name: str) -> Optional[str]:
    """The install path a ``*_project`` / ``*_page`` / ``*_library`` role names, or None when
    it is not set or not there (for module constants and tools)."""
    v = role_value(name)
    if not isinstance(v, str):
        return None
    p = install_path(v)
    return p if os.path.exists(p) else None


def missing(*names: str) -> Optional[str]:
    """Why these roles cannot be used (the first one not set, or a path role whose file is
    not installed), or None when all can."""
    for name in names:
        v = role_value(name)
        if v is None:
            return not_configured(name)
        if is_path_role(name):
            for rel in v if isinstance(v, list) else [v]:
                if not isinstance(rel, str) or not os.path.exists(install_path(rel)):
                    return "not installed: %s = %s" % (name, rel)
    return None


def role(name: str):
    """The role's value, or skip the test (not set, or a path role whose file is not
    installed). A path role's value is given as written: :func:`role_path` gives the path."""
    why = missing(name)
    if why:
        import pytest

        pytest.skip(why)
    return role_value(name)


def role_path(name: str) -> str:
    """The install path of a ``*_project`` / ``*_page`` / ``*_library`` role, or skip."""
    return install_path(role(name))


def role_paths(name: str) -> List[str]:
    """The install paths of a ``*_pages`` role (a list), or skip."""
    v = role(name)
    return [install_path(r) for r in (v if isinstance(v, list) else [v])]


def need_roles(*names: str):
    """A skipif mark for a test (or a module's ``pytestmark``) that needs these roles."""
    import pytest

    why = missing(*names)
    return pytest.mark.skipif(why is not None, reason=why or "")


def role_params(name: str, arity: int = 1) -> list:
    """The items of a list role for ``pytest.mark.parametrize`` (a list item becomes a
    tuple), or one skipped parameter set of ``arity`` values when the role is not set."""
    import pytest

    v = role_value(name)
    if not isinstance(v, list):
        why = not_configured(name) if v is None else "%s in local.json is not a list" % name
        return [pytest.param(*([None] * arity), marks=pytest.mark.skip(reason=why))]
    return [tuple(x) if isinstance(x, list) else x for x in v]


def _strings(value) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        return [s for x in value for s in _strings(x)]
    return []


def files_of_role(name: str) -> List[str]:
    """The install files a role names (every 'Project/File.ctg' in its value), relative to
    the User folder with the OS separator, as :func:`files_named` gives them."""
    return sorted(
        {
            os.path.join(*re.split(r"[\\/]+", s.strip().strip("\\/")))
            for s in _strings(roles().get(name))
            if _ROLE_FILE.match(s.strip())
        }
    )


def role_files() -> List[str]:
    """Every install file the local roles name (tools/pin_corpus.py pins them)."""
    return sorted({f for name in roles() for f in files_of_role(name)})


def roles_in(text: str) -> List[str]:
    """The configured roles a test source names (as a quoted string)."""
    have = roles()
    if not have:
        return []
    return sorted({m.group(1) for m in re.finditer(r"""["']([A-Za-z]\w*)["']""", text) if m.group(1) in have})


# --------------------------------------------------------------------------- the install
def user_projects() -> List[str]:
    """The install's project folders that AVEVA does not ship (the user's own), sorted."""
    try:
        names = os.listdir(R2_USER)
    except OSError:
        return []
    return sorted(
        n for n in names if n.lower() not in SHIPPED_PROJECTS and os.path.isdir(os.path.join(R2_USER, n))
    )


def user_files(ext: str = ".ctg") -> List[str]:
    """Every file with this extension in the local projects (absolute paths, sorted)."""
    out = []
    for p in user_projects():
        d = os.path.join(R2_USER, p)
        try:
            out.extend(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(ext.lower()))
        except OSError:
            continue
    return sorted(out)


def biggest_user_file(ext: str = ".ctg") -> Optional[str]:
    """The biggest file with this extension in the local projects (a big page for a timing),
    or None when there is none."""
    best, size = None, -1
    for f in user_files(ext):
        try:
            n = os.path.getsize(f)
        except OSError:
            continue
        if n > size:
            best, size = f, n
    return best


def names_in(text: str) -> List[str]:
    """The page / library file names a test source names."""
    return sorted({m.group(1).lower() for m in _NAME.finditer(text)})


def files_named(name: str) -> List[str]:
    """Every R2 file with this name (any project), relative to the User folder."""
    out = []
    try:
        projects = os.listdir(R2_USER)
    except OSError:
        return out
    for p in projects:
        d = os.path.join(R2_USER, p)
        try:
            for f in os.listdir(d):
                if f.lower() == name:
                    out.append(os.path.join(p, f))
        except OSError:
            continue
    return sorted(out)


def sha1(rel: str) -> str:
    path = os.path.join(R2_USER, rel)
    try:
        st = os.stat(path)
    except OSError:
        return ""
    hit = _sha.get(rel)
    if hit is not None and hit[0] == (st.st_mtime_ns, st.st_size):
        return hit[1]
    with open(path, "rb") as fh:
        h = hashlib.sha1(fh.read()).hexdigest()
    _sha[rel] = ((st.st_mtime_ns, st.st_size), h)
    return h


def pins() -> Dict[str, str]:
    if not _pins and os.path.isfile(PINS_FILE):
        with open(PINS_FILE, encoding="utf-8") as fh:
            _pins.update(json.load(fh))
    return _pins


def changed_inputs(source: str) -> List[str]:
    """The pinned files ``source`` names (by file name, or through a role it names) whose
    content changed since the pin."""
    p = pins()
    rels = [rel for name in names_in(source) for rel in files_named(name)]
    rels += [rel for name in roles_in(source) for rel in files_of_role(name)]
    out = []
    for rel in rels:
        key = rel.replace("\\", "/")
        if key in p and key not in out and sha1(rel) != p[key]:
            out.append(key)
    return out


def text_read_by(item) -> Optional[str]:
    """What the failing test reads."""
    import ast
    import inspect
    import textwrap

    fn = getattr(item, "function", None)
    mod = getattr(item, "module", None)
    try:
        own = inspect.getsource(fn)
        msrc = inspect.getsource(mod)
    except Exception:  # noqa: BLE001
        return None
    parts = [own]
    cs = getattr(item, "callspec", None)
    if cs is not None:
        parts.append(repr(cs.params))
    tree = ast.parse(msrc)
    defs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        seg = ast.get_source_segment(msrc, node) or ""
        for n in names:
            defs[n] = seg

    def names_of(text):
        try:
            t = ast.parse(textwrap.dedent(text))
        except SyntaxError:
            return set()
        return {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}

    todo = names_of(own) | set(getattr(item, "fixturenames", ()) or ())
    seen = set()
    for _depth in range(4):
        nxt = set()
        for n in todo - seen:
            seen.add(n)
            seg = defs.get(n)
            if seg:
                parts.append(seg)
                nxt |= names_of(seg)
        todo = nxt
    return "\n".join(parts)


def report(item, rep) -> None:
    """The body of the conftests' ``pytest_runtest_makereport`` hook wrapper: a failed call
    whose inputs changed since their pin becomes SKIPPED 'input changed' (tools/run_tests.py
    counts those apart). Re-pin with tools/pin_corpus.py once the expectations are checked
    against the new content."""
    if rep.when != "call" or not rep.failed:
        return
    if os.environ.get("CTG_HERMETIC") == "1":
        return  # (a hermetic test reads no corpus: nothing of it can have changed)
    try:
        src = text_read_by(item)
        if src is None:
            with open(str(item.fspath), encoding="utf-8") as fh:
                src = fh.read()
        changed = changed_inputs(src)
    except Exception:  # noqa: BLE001 -- the failure stands
        return
    if changed:
        rep.outcome = "skipped"
        rep.longrepr = (
            str(item.fspath),
            item.location[1] or 0,
            "input changed: %s (re-pin with tools/pin_corpus.py)" % ", ".join(changed[:4]),
        )
