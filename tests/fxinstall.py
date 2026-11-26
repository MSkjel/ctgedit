"""The fake install in tests/fixtures/FxInstall."""

from __future__ import annotations

import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
#: the committed fixtures (never written by a test: copy them first)
FIXTURES = os.path.join(HERE, "fixtures")
FX_INSTALL = os.path.join(FIXTURES, "FxInstall")
#: the projects of the fake install
PROJECTS = (
    "Include",
    "System",
    "FxStyle",
    "FxLib",
    "FxMain",
    "FxOther",
    "FxSim",
    "FxPerf",
    "FxSa",
    "FxCycleA",
    "FxCycleB",
    "FxDemo",
)


class FxInstall:
    """A copy of the fake install: its folders and the files of its projects."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.user = os.path.join(self.root, "User")
        self.config = os.path.join(self.root, "Config", "citect.ini")
        self.bin = os.path.join(self.root, "Bin")

    def project(self, name: str = "FxMain") -> str:
        """The folder of project ``name`` (FxMain, FxLib, FxStyle, Include ...)."""
        path = os.path.join(self.user, name)
        if not os.path.isdir(path):
            raise KeyError("the fake install has no project %r" % name)
        return path

    def page(self, name: str, project: str = "FxMain") -> str:
        """The .ctg of page ``name`` (no extension) of ``project``."""
        path = os.path.join(self.project(project), name + ".ctg")
        if not os.path.isfile(path):
            raise KeyError("project %s has no page %r" % (project, name))
        return path

    def file(self, project: str, name: str) -> str:
        """Another file of a project (a library, a table: 'fxgenie.ctm', 'pages.DBF')."""
        path = os.path.join(self.project(project), name)
        if not os.path.exists(path):
            raise KeyError("project %s has no file %r" % (project, name))
        return path

    def new_project(self, name: str, includes=("FxLib", "FxStyle"), tables=True) -> str:
        """A new empty project folder in the install, including ``includes`` (an
        include.DBF with those rows) and, with ``tables``, FxMain's page tables emptied
        (pages.DBF, the pg tables, changes.DBF and their indexes)."""
        from ctgedit.format import dbf

        path = os.path.join(self.user, name)
        os.makedirs(path)
        src = self.project("FxMain")
        with dbf.Dbf(os.path.join(src, "include.DBF"), writable=False) as inc:
            fields = [(n, w) for n, _o, w in inc.fields]
        dbf.create_table(os.path.join(path, "include.DBF"), fields, [{"NAME": n} for n in includes])
        if tables:
            for f in sorted(os.listdir(src)):
                low = f.lower()
                stem = os.path.splitext(low)[0]
                if low.endswith(".dbf") and (
                    stem.startswith("pg") or stem in ("pages", "changes", "activex")
                ):
                    with dbf.Dbf(os.path.join(src, f), writable=False) as t:
                        fields = [(n, w) for n, _o, w in t.fields]
                    nd = os.path.join(src, os.path.splitext(f)[0] + ".NDX")
                    key = None
                    if os.path.exists(nd):
                        with dbf.Ndx(nd, writable=False) as x:
                            key = x.expr
                    dbf.create_table(os.path.join(path, f), fields, indexes=[key] if key else ())
        return path

    def write_func0(self, rows=None) -> str:
        """A stand-in ``Bin/FUNC0.DBF`` (the built-in function table: NAME, ARGNAMES) in
        the copy, for a test of argument names. The fake install has none of its own (the
        engine and the catalogue then know no built-in). ``rows`` = [(name, argnames)];
        the default is a few functions the fixtures' Cicode calls."""
        from ctgedit.format import dbf

        if rows is None:
            rows = FUNC0_ROWS
        path = os.path.join(self.bin, "FUNC0.DBF")
        dbf.create_table(
            path, [("NAME", 32), ("ARGNAMES", 128)], [{"NAME": n, "ARGNAMES": a} for n, a in rows]
        )
        return path

    def big_library(self, project: str, library: str, count: int, prefix: str = "fx_gen") -> str:
        """A symbol library ``<project>/<library>`` of ``count`` elements named
        ``<prefix>0001`` ..., each a copy of FxLib's fxsym.fx_box (for the tests that need
        thousands of symbols on a chain: made in the test, never committed)."""
        from ctgedit.format import element as E

        src = E.LibraryFile.open(self.file("FxLib", "fxsym.ctl"))
        content = src.block_data(src.find("fx_box"))
        path = os.path.join(self.project(project), library)
        lib = E.LibraryFile.open(path) if os.path.exists(path) else E.LibraryFile.new()
        for n in range(1, count + 1):
            lib.write_element("%s%04d" % (prefix, n), content)
        E._write_library(path, lib)
        return path


#: write_func0's default rows: (name, argument text as the function list shows it)
FUNC0_ROWS = (
    ("PageDisplay", "(Page)"),
    ("WinFree", "()"),
    ("WinNewAt", "(Page, X, Y, Mode)"),
    ("Sleep", "(Seconds)"),
    ("IntToStr", "(Number)"),
)


# --------------------------------------------------------------------------- the oracle
#: the one-off CtDraw capture of the fixtures (tools/live/fixture_oracle.py writes it:
# <step>/<unit>/<file> and manifest.json)
ORACLE = os.path.join(FIXTURES, "oracle")
ORACLE_MANIFEST = os.path.join(ORACLE, "manifest.json")
#: the manifest as last read: (mtime_ns, size, parsed)
_MANIFEST: list = []


def oracle_safe_name(name: str) -> str:
    """A unit's folder name as the capture writes it (fixture_oracle.safe_name: letters,
    digits and ``_@.!-`` stay, every other character becomes ``%XX`` of its code page
    1252 bytes)."""
    out = []
    for ch in name:
        if ch.isascii() and (ch.isalnum() or ch in "_@.!-"):
            out.append(ch)
        else:
            out.extend("%%%02X" % b for b in ch.encode("cp1252", "replace"))
    return "".join(out)


def oracle_manifest() -> dict:
    """The capture's manifest.json ({} until the capture is committed)."""
    import json

    try:
        st = os.stat(ORACLE_MANIFEST)
    except OSError:
        return {}
    if _MANIFEST and _MANIFEST[0][:2] == (st.st_mtime_ns, st.st_size):
        return _MANIFEST[0][2]
    with open(ORACLE_MANIFEST, encoding="utf-8") as fh:
        data = json.load(fh)
    _MANIFEST[:] = [(st.st_mtime_ns, st.st_size, data)]
    return data


def oracle_rel(step: str, unit: str, name: str) -> str:
    """The manifest's ``file`` of a capture file: ``<step>/<unit folders>/<name>``."""
    parts = [step] + [oracle_safe_name(p) for p in unit.split("/")] + name.split("/")
    return "/".join(parts)


def oracle(step: str, unit: str, name: str) -> str:
    """The path of a captured oracle file."""
    import pytest

    where = "%s/%s" % (step, unit)
    waiting = "waiting for the oracle capture: %s" % where
    man = oracle_manifest()
    if not man:
        pytest.skip(waiting)
    u = man.get("units", {}).get("%s:%s" % (step, unit))
    if u is None:
        pytest.skip(waiting)
    if u.get("status") != "captured":
        note = u.get("note") or ""
        pytest.skip("%s (its last run %s%s)" % (waiting, u.get("status", "?"), ": " + note if note else ""))
    rel = oracle_rel(step, unit, name)
    entry = next((e for e in man.get("files", ()) if e.get("file") == rel), None)
    if entry is None:
        pytest.skip("%s (the capture made no %s)" % (waiting, name))
    if entry.get("withheld"):
        pytest.skip("oracle withheld: %s (%s)" % (rel, entry["withheld"]))
    src, want = entry.get("source"), entry.get("source_sha1")
    if src and want and not src.startswith("inputs/"):
        now = _sha1_of(os.path.join(FX_INSTALL, "User", src.replace("/", os.sep)))
        if now != want:
            pytest.skip(
                "oracle stale: %s (the fixture %s changed since the capture: run "
                "tools/live/fixture_oracle.py run --steps %s:%s)" % (rel, src, step, unit)
            )
    path = os.path.join(ORACLE, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        pytest.skip("%s (%s is not in the checkout)" % (waiting, rel))
    if entry.get("sha1") and _sha1_of(path) != entry["sha1"]:
        pytest.fail(
            "the oracle file %s differs from its manifest sha1: never edit a capture by hand" % rel,
            pytrace=False,
        )
    return path


def oracle_bytes(step: str, unit: str, name: str) -> bytes:
    """The bytes of :func:`oracle` (skips the same way)."""
    with open(oracle(step, unit, name), "rb") as fh:
        return fh.read()


def oracle_json(step: str, unit: str, name: str):
    """A captured JSON file of :func:`oracle`, parsed (``<FX>`` stands for the install's
    User folder in its text)."""
    import json

    with open(oracle(step, unit, name), encoding="utf-8") as fh:
        return json.load(fh)


def oracle_dialogs(step: str, unit: str) -> list:
    """The message boxes CtDraw showed during a unit (the manifest's ``dialogs``: the text,
    then its buttons in brackets)."""
    u = oracle_manifest().get("units", {}).get("%s:%s" % (step, unit)) or {}
    return list(u.get("dialogs") or ())


#: CtDraw's message box for a control it cannot create (the fixtures' made-up
#: records.FX_CONTROL_CLSID is registered nowhere; one box per control)
NOT_A_CONTROL = "This is not a valid ActiveX Control."


def as_ctdraw_names_controls(rows):
    """activex.DBF rows as CtDraw registers them through COM."""
    return [dict(r, OBJ_NAME="") for r in rows]


def _sha1_of(path: str):
    import hashlib

    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return None


#: the fake install's (folders, (source, target-relative) files), listed once per process
_TREE: list = []


def _copy_tree(src: str, dst: str) -> None:
    """shutil.copytree's result (file times kept: the .ctF and oracle checks compare them),
    in threads: the cost is per file, and the install has about 800 small ones. The
    collector is off meanwhile: it never runs on a worker thread (infra/gcguard)."""
    import gc
    from concurrent.futures import ThreadPoolExecutor

    if not _TREE:
        dirs, files = [], []
        for dp, _dn, fn in os.walk(src):
            rel = os.path.relpath(dp, src)
            dirs.append(rel)
            files += [(os.path.join(dp, f), os.path.join(rel, f)) for f in fn]
        _TREE[:] = [dirs, files]
    dirs, files = _TREE
    for rel in dirs:
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
    was_on = gc.isenabled()
    gc.disable()
    try:
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda f: shutil.copy2(f[0], os.path.join(dst, f[1])), files))
    finally:
        if was_on:
            gc.enable()


def install_copy(dest_parent: str) -> FxInstall:
    """Copy the fake install to ``<dest_parent>\\FxInstall`` (``dest_parent`` must hold
    nothing else: projects.sibling_install_user_dirs reads its neighbours), point
    MASTER.DBF's paths and citect.ini's [CtEdit] USER at the copy. Returns it."""
    if not os.path.isdir(FX_INSTALL):
        raise FileNotFoundError("tests/fixtures/FxInstall is missing: run tools/make_fixtures.py")
    root = os.path.join(dest_parent, "FxInstall")
    _copy_tree(FX_INSTALL, root)
    fx = FxInstall(root)
    _rewrite_master(os.path.join(fx.user, "MASTER.DBF"), fx.user)
    with open(fx.config, "rb") as fh:
        text = fh.read().decode("cp1252")
    text = text.replace("[CtEdit]\r\n", "[CtEdit]\r\nUSER=%s\r\n" % fx.user, 1)
    with open(fx.config, "wb") as fh:
        fh.write(text.encode("cp1252"))
    return fx


class Snapshot:
    """An install copy as it was (every file's bytes and times), to put it back after a
    test: far cheaper than a new copy, which costs about 800 file creations."""

    def __init__(self, root: str):
        self.root = root
        self.dirs: set = set()
        #: {normcase relative path: (bytes, (atime_ns, mtime_ns), mode)}
        self.files: dict = {}
        #: {normcase relative path: its spelling}: a renamed file gets its spelling back
        self.names: dict = {}
        for dp, _dn, fn in os.walk(root):
            rel = os.path.relpath(dp, root)
            self.dirs.add(os.path.normcase(rel))
            for f in fn:
                p = os.path.join(dp, f)
                st = os.stat(p)
                self.names[os.path.normcase(os.path.join(rel, f))] = os.path.join(rel, f)
                with open(p, "rb") as fh:
                    self.files[os.path.normcase(os.path.join(rel, f))] = (
                        fh.read(),
                        (st.st_atime_ns, st.st_mtime_ns),
                        st.st_mode,
                    )

    def restore(self) -> bool:
        """Put the copy back as it was. True when something had changed. Raises OSError
        when a file cannot be put back (held open by a process the test left)."""
        changed = False
        seen = set()
        # the bottom folders first: what a test added goes before its folder
        for dp, dn, fn in os.walk(self.root, topdown=False):
            rel = os.path.relpath(dp, self.root)
            for f in fn:
                key = os.path.normcase(os.path.join(rel, f))
                p = os.path.join(dp, f)
                was = self.files.get(key)
                if was is None:
                    os.chmod(p, 0o666)
                    os.remove(p)
                    changed = True
                    continue
                seen.add(key)
                spelt = os.path.join(self.root, self.names[key])
                if f != os.path.basename(spelt):
                    os.rename(p, spelt)  # (a change of case only)
                    p = spelt
                    changed = True
                st = os.stat(p)
                if st.st_mtime_ns == was[1][1] and st.st_size == len(was[0]) and st.st_mode == was[2]:
                    with open(p, "rb") as fh:
                        if fh.read() == was[0]:
                            continue
                self._write(p, was)
                changed = True
            if os.path.normcase(rel) not in self.dirs:
                os.rmdir(dp)
                changed = True
        for d in self.dirs:
            p = os.path.join(self.root, d)
            if not os.path.isdir(p):
                os.makedirs(p)
                changed = True
        for key, was in self.files.items():
            if key not in seen:
                self._write(os.path.join(self.root, self.names[key]), was)
                changed = True
        return changed

    @staticmethod
    def _write(path: str, was) -> None:
        if os.path.exists(path):
            os.chmod(path, 0o666)
        with open(path, "wb") as fh:
            fh.write(was[0])
        os.chmod(path, was[2])
        os.utime(path, ns=was[1])


def _rewrite_master(path: str, user: str) -> None:
    """MASTER.DBF's PATH of each project = ``<user>\\<NAME>``."""
    from ctgedit.format.dbf import Dbf

    with Dbf(path, writable=False) as d:
        name_at, name_len = d.fmap["NAME"]
        path_at, path_len = d.fmap["PATH"]
        hlen, rlen, n = d.hlen, d.rlen, d.nrec
    with open(path, "r+b") as fh:
        for i in range(n):
            fh.seek(hlen + i * rlen)
            rec = bytearray(fh.read(rlen))
            name = bytes(rec[name_at : name_at + name_len]).decode("cp1252").strip()
            new = os.path.join(user, name).encode("cp1252")
            if len(new) > path_len:
                raise ValueError("the copy's path is too long for MASTER.DBF: %s" % new)
            rec[path_at : path_at + path_len] = new.ljust(path_len)
            fh.seek(hlen + i * rlen)
            fh.write(bytes(rec))
