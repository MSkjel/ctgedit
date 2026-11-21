"""The Cicode functions an expression can call (Insert Function)."""

from __future__ import annotations

import html
import json
import os
import re
import threading
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from ..format.textcodec import CODEC


@dataclass(frozen=True)
class CicodeFunction:
    name: str
    signature: str = ""  # 'INT AlarmAck(INT Mode, INT Value [, STRING ClusterName])'
    description: str = ""
    category: str = ""  # help category, or the .ci file for a project function
    source: str = "built-in"  # 'built-in' or the project name
    args: str = ""  # CtDraw's argument text: '(Mode, Value [,ClusterName])'

    def insert_text(self, arguments: bool = True) -> str:
        """What Insert Function puts at the cursor."""
        if not self.args:
            return self.name
        return self.name + (self.args if arguments else "()")


def _help_dir() -> Optional[str]:
    """The installed help's folder (CTG_AVEVA_HELP wins), None with no install."""
    from ..infra.paths import aveva_docs

    return aveva_docs()


def _bin_dirs() -> List[str]:
    """infra.paths.scada_bin_dirs (kept under this name for its callers)."""
    from ..infra.paths import scada_bin_dirs

    return scada_bin_dirs()


_CTX = re.compile(rb"([A-Za-z_][A-Za-z0-9_]{0,63})_Cicode_function\x00")


def _names_from_ctedit() -> List[str]:
    for d in _bin_dirs():
        p = os.path.join(d, "ctedit32.dll")
        try:
            data = open(p, "rb").read()
        except OSError:
            continue
        names = []
        seen = set()
        for m in _CTX.finditer(data):
            n = m.group(1).decode("ascii")
            if n.lower() not in seen:
                seen.add(n.lower())
                names.append(n)
        if names:
            return names
    return []


def _argnames_from_func0() -> Dict[str, str]:
    """name (lower case) -> ARGNAMES of the install's ``Bin\\FUNC0.DBF`` (the
    built-in function table): the argument text the Graphics Builder's Insert
    Function shows and inserts, '(Mode, Value [,ClusterName])'."""
    from ..format.dbf import Dbf

    for d in _bin_dirs():
        p = os.path.join(d, "FUNC0.DBF")
        if not os.path.isfile(p):
            continue
        try:
            with Dbf(p, writable=False) as t:
                if "ARGNAMES" not in t.fmap or "NAME" not in t.fmap:
                    continue
                out: Dict[str, str] = {}
                for i in range(t.nrec):
                    rec = t.read(i)
                    if len(rec) < t.rlen or rec[:1] == b"*":
                        continue
                    name, args = t.field(rec, "NAME").strip(), t.field(rec, "ARGNAMES").strip()
                    if name and args.startswith("(") and args.endswith(")"):
                        out.setdefault(name.lower(), args)
        except OSError, ValueError, KeyError:
            continue
        if out:
            return out
    return {}


_ARG_NAME = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*$")


def _declared_argnames(args: str) -> str:
    """'(STRING sFile, INT Mode = 0)' of a .ci declaration -> the argument
    text the Graphics Builder inserts for it, '(sFile, Mode)'."""
    names, part, in_str = [], [], False
    for c in args + ",":
        if c == '"':
            in_str = not in_str
        if c == "," and not in_str:
            decl = "".join(part).split("=", 1)[0]
            m = _ARG_NAME.search(decl)
            if m:
                names.append(m.group(1))
            part = []
        else:
            part.append(c)
    return "(" + ", ".join(names) + ")"


_SYNTAX = re.compile(r'<p class="subheading">Syntax</p>\s*<p class="paragraph">(.*?)</p>', re.S)
_FIRST = re.compile(r"</h5>\s*<p class=\"paragraph\">(.*?)</p>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return html.unescape(" ".join(_TAG.sub("", fragment).split()))


def _from_help() -> Dict[str, CicodeFunction]:
    root = _help_dir()
    if not root:
        return {}
    try:
        toc = json.load(open(os.path.join(root, "toc.json"), encoding="utf-8"))
    except OSError, ValueError:
        return {}

    def find(node, name):
        if isinstance(node, dict):
            if node.get("name") == name:
                return node
            node = node.get("items") or []
        for k in node if isinstance(node, list) else []:
            hit = find(k, name)
            if hit is not None:
                return hit
        return None

    cats = find(toc.get("items") or [], "Cicode Function Categories")
    if cats is None:
        return {}
    leaves: List[Tuple[str, str, str]] = []

    def walk(node, cat):
        for k in node.get("items") or []:
            if k.get("items"):
                walk(k, cat)
            else:
                leaves.append((cat, k.get("name", ""), str(k.get("id", ""))))

    for c in cats.get("items") or []:
        walk(c, c.get("name", "").replace(" Functions", ""))
    out: Dict[str, CicodeFunction] = {}
    for cat, name, tid in leaves:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
            continue  # 'Conversion Table', '... Fields'
        try:
            text = open(os.path.join(root, tid + ".html"), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        m = _SYNTAX.search(text)
        sig = _clean(m.group(1)) if m else ""
        d = _FIRST.search(text)
        out.setdefault(
            name.lower(), CicodeFunction(name, sig, _clean(d.group(1)) if d else "", cat, "built-in")
        )
    return out


_BUILTIN_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _builtin_cached() -> Tuple[CicodeFunction, ...]:
    helped = _from_help()
    names = _names_from_ctedit()
    out: List[CicodeFunction] = []
    seen = set()
    for n in names:
        f = helped.get(n.lower())
        out.append(f if f is not None else CicodeFunction(n, n + "()"))
        seen.add(n.lower())
    for low, f in sorted(helped.items()):
        if low not in seen:
            out.append(f)
    argnames = _argnames_from_func0()
    if argnames:
        out = [replace(f, args=argnames[f.name.lower()]) if f.name.lower() in argnames else f for f in out]
    out.sort(key=lambda f: f.name.lower())
    return tuple(out)


def builtin_functions() -> Tuple[CicodeFunction, ...]:
    """The built-in Cicode functions (sorted by name)."""
    with _BUILTIN_LOCK:
        return _builtin_cached()


def builtins_loading() -> bool:
    """True while another thread (:func:`preload`) is still reading the
    built-in list: a lookup that must not wait (the advice shown after an
    edit) can then say 'unknown' instead of blocking the UI."""
    return _builtin_cached.cache_info().currsize == 0 and _BUILTIN_LOCK.locked()


_PRELOADED: set = set()


def preload(project_dir: Optional[str] = None) -> Optional[threading.Thread]:
    """Read the built-in list -- and the functions of ``project_dir``'s
    include chain -- in a background thread (a cold help read can take
    seconds); the lookups then return at once. Once per process / project."""
    key = os.path.normcase(os.path.abspath(project_dir)) if project_dir else ""
    if key in _PRELOADED:
        return None
    _PRELOADED.add(key)

    def work():
        try:
            builtin_functions()
            if project_dir:
                project_functions(project_dir)
        except Exception:  # noqa: BLE001 -- a warm-up only
            pass

    from ..infra import gcguard

    gcguard.before_worker()  # in the editor: only the GUI thread collects
    t = threading.Thread(target=work, name="cicode-catalog", daemon=True)
    t.start()
    return t


# -- project functions ----------------------------------------------------------
# a string, a '/* */' block, or a '//' / '!' line comment -- whichever starts
# first (one left-to-right pass: the '/*' inside a '//*****' banner line opens
# nothing, and a comment marker inside a string is text)
_TOKENS = re.compile(r'"[^"\n]*"?|(?s:/\*.*?(?:\*/|\Z))|//[^\n]*|![^\n]*')
_DECL = re.compile(
    r"^[ \t]*(?:(?P<scope>PUBLIC|PRIVATE)\s+)?"
    r"(?:(?P<type>INT|REAL|STRING|OBJECT|QUALITY|TIMESTAMP|LONG|DIGITAL)\s+)?"
    r"FUNCTION\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>[^)]*)\)",
    re.I | re.M,
)


def _strip_comments(src: str) -> str:
    """Cicode comments: '/* */', '//' and '!' to the end of the line, in one
    pass with the strings. A block comment keeps its line breaks."""

    def repl(m):
        t = m.group(0)
        if t.startswith('"'):
            return t
        return " " + "\n" * t.count("\n") if t.startswith("/*") else ""

    return _TOKENS.sub(repl, src)


def parse_ci(text: str, source: str = "", file: str = "") -> List[CicodeFunction]:
    """The callable functions a .ci file declares (PRIVATE ones skipped)."""
    out = []
    for m in _DECL.finditer(_strip_comments(text)):
        if (m.group("scope") or "").upper() == "PRIVATE":
            continue
        args = " ".join(m.group("args").split())
        rtype = (m.group("type") or "").upper()
        sig = "%s%s(%s)" % (rtype + " " if rtype else "", m.group("name"), args)
        out.append(CicodeFunction(m.group("name"), sig, "", file, source, _declared_argnames(args)))
    return out


_CI_CACHE: Dict[str, tuple] = {}


def _ci_functions(path: str, source: str) -> List[CicodeFunction]:
    try:
        st = os.stat(path)
    except OSError:
        return []
    sig = (st.st_mtime_ns, st.st_size)
    hit = _CI_CACHE.get(path)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        with open(path, "rb") as f:
            text = f.read().decode(CODEC, "replace").replace("\r\n", "\n")
    except OSError:
        return []
    funcs = parse_ci(text, source, os.path.basename(path))
    _CI_CACHE[path] = (sig, funcs)
    return funcs


def _scope_ci_files(project_dir: str) -> List[Tuple[str, str]]:
    """(path, project name) of the .ci files of the page folder's tag scope, in scope
    order, each project's sorted by name."""
    from .tagscope import scope

    out = []
    for p in scope(project_dir).projects:
        d, proj = p.dir, p.name
        try:
            files = sorted(f for f in os.listdir(d) if f.lower().endswith(".ci"))
        except OSError:
            continue
        out += [(os.path.join(d, f), proj) for f in files]
    return out


def ci_stamps(project_dir: str) -> tuple:
    """((path, mtime_ns, size), ...) of the .ci files of the page folder's tag scope: what
    :func:`project_functions` reads. Two equal answers = the same functions (a memo of
    them is current while its stamps are these)."""
    out = []
    for path, _proj in _scope_ci_files(project_dir):
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append((path, st.st_mtime_ns, st.st_size))
    return tuple(out)


def project_functions(project_dir: str) -> Tuple[CicodeFunction, ...]:
    """The public functions of the .ci files of the page folder's tag scope
    (its project, the projects it includes and the projects compiled with it:
    io.tagscope), first definition wins."""
    out, seen = [], set()
    for path, proj in _scope_ci_files(project_dir):
        for fn in _ci_functions(path, proj):
            if fn.name.lower() not in seen:
                seen.add(fn.name.lower())
                out.append(fn)
    return tuple(out)


class FunctionCatalog:
    """Built-in + project functions of one project (for the picker and the
    'unknown function' advice)."""

    def __init__(self, project_dir: Optional[str] = None, builtins=None):
        self.project_dir = project_dir
        self._builtins = builtins
        self._project = None
        self._project_stamps = None
        self._names = None

    def builtins(self) -> Tuple[CicodeFunction, ...]:
        if self._builtins is None:
            self._builtins = builtin_functions()
        return self._builtins

    def project(self) -> Tuple[CicodeFunction, ...]:
        """The project's functions, read again when a .ci file of the scope changed since
        (:func:`ci_stamps`; the per-file parses are cached)."""
        if not self.project_dir:
            return ()
        stamps = ci_stamps(self.project_dir)
        if self._project is None or stamps != self._project_stamps:
            self._project = project_functions(self.project_dir)
            self._project_stamps = stamps
            self._names = None
        return self._project

    def all(self) -> List[CicodeFunction]:
        seen, out = set(), []
        for f in list(self.project()) + list(self.builtins()):
            if f.name.lower() not in seen:
                seen.add(f.name.lower())
                out.append(f)
        out.sort(key=lambda f: f.name.lower())
        return out

    def get(self, name: str) -> Optional[CicodeFunction]:
        low = (name or "").lower()
        return next((f for f in self.all() if f.name.lower() == low), None)

    def is_function(self, name: str) -> Optional[bool]:
        """True / False; None when no function list is available at all (or
        the built-in list is still being read in the background)."""
        project = self.project()  # (a changed .ci file drops the names held)
        if self._names is None and self._builtins is None and builtins_loading():
            low = (name or "").lower()
            return True if any(f.name.lower() == low for f in project) else None
        if self._names is None:
            self._names = {f.name.lower() for f in self.project()} | {f.name.lower() for f in self.builtins()}
        if not self._names:
            return None
        return (name or "").lower() in self._names


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("cicode_catalog.files", _CI_CACHE, on=("refresh", "tests"))
_caches.register("cicode_catalog.builtins", _builtin_cached, on=("refresh", "tests"))


_caches.register("cicode_catalog.preloaded", _PRELOADED, on=("refresh", "tests"))
