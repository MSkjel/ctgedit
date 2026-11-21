"""Capture the fixtures through the real CtDraw, once.

python tools/live/fixture_oracle.py --list
python tools/live/fixture_oracle.py run --dry-run
python tools/live/fixture_oracle.py run [--steps a,b:c*]   (Studio must be closed)
python tools/live/fixture_oracle.py restore [--backup <dir>] [--stop-studio] [--force]
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (REPO, os.path.join(REPO, "tools"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: the fake install the capture copies from (tools/make_fixtures.py writes it)
FIXTURES = os.path.join(REPO, "tests", "fixtures", "FxInstall")
#: where the capture writes (``--out`` overrides)
ORACLE = os.path.join(REPO, "tests", "fixtures", "oracle")
#: the capture's version (the state file and the manifest record it)
TOOL_VERSION = 2

#: (fixture project, its name in the install); Include and System get Fx names so the
#: install's own Include and System stay what they are
PROJECTS: Tuple[Tuple[str, str], ...] = (
    ("Include", "FxInclude"),
    ("System", "FxSystem"),
    ("FxStyle", "FxStyle"),
    ("FxLib", "FxLib"),
    ("FxMain", "FxMain"),
    ("FxOther", "FxOther"),
    ("FxSim", "FxSim"),
    ("FxSa", "FxSa"),
)
TARGETS = tuple(t for _s, t in PROJECTS)
SOURCE_OF = {t: s for s, t in PROJECTS}
#: appended to each copied project's include.DBF (not to themselves)
INCLUDE_ROWS = ("FxInclude", "FxSystem")
MARKER = ".fixture_oracle"
#: the programs that must not run while the install changes
WATCHED = ("CitectIDE.exe", "CtDraw32.exe", "CtCmp32.exe")
#: what the capture's pages say about themselves (the state file's 'tool')
TOOL = "tools/live/fixture_oracle.py"
#: the tries of a folder delete and the pause between them, in seconds (tests: short)
RMTREE_TRIES = 6
RMTREE_PAUSE = 2.0
#: the citect.ini keys a run sets (section, key), whatever their value (the restore's
#: default when a state file names none)
RUN_INI_KEYS = (("CtEdit", "LastDatabase"), ("CtEdit", "LastDatabasePath"), ("Animator", "FastDisplay"))
#: citect.ini sections CtDraw and Studio write while they run (window places, recent files,
#: the Graphics Builder's last choices): a difference there does not stop a restore
INI_SESSION_SECTIONS = ("ctdraw", "recent file list", "ctexplore")
#: [CtEdit] keys Studio writes while it runs
INI_SESSION_KEYS = (("ctedit", "window"), ("ctedit", "lastpage"))


class Refused(RuntimeError):
    """The tool will not start (or not restore): the message says why."""


class UnitFailed(RuntimeError):
    """A capture unit could not do what it set out to do."""


class BuilderLost(RuntimeError):
    """No fresh Graphics Builder: the old one would not end or a new one would not attach.
    The run stops (and restores)."""


# ============================================================================ small helpers
def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def file_sha1(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return sha1(fh.read())
    except OSError:
        return None


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _write(path: str, data: bytes) -> None:
    """Write ``path`` through ``path.tmp``; the tmp file does not stay when it fails."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def write_json(path: str, obj) -> None:
    _write(path, (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_name(name: str) -> str:
    """A file name for an element or page name: letters, digits and ``_@.!-`` stay, every
    other character becomes ``%XX`` of its code page 1252 bytes (deterministic)."""
    out = []
    for ch in name:
        if ch.isascii() and (ch.isalnum() or ch in "_@.!-"):
            out.append(ch)
        else:
            out.extend("%%%02X" % b for b in ch.encode("cp1252", "replace"))
    return "".join(out)


def _find_file(folder: str, name: str) -> Optional[str]:
    """``folder/name`` without case (CtDraw writes .ctF or .CTF)."""
    try:
        for f in os.listdir(folder):
            if f.lower() == name.lower():
                return os.path.join(folder, f)
    except OSError:
        pass
    return None


def _say(*parts) -> None:
    print(*parts, flush=True)


# ============================================================================ the install
@dataclass
class Install:
    root: str
    user: str
    ini: str
    #: the install's Bin folder (citect.frm: the forms CtDraw checks the tables against)
    bin: Optional[str] = None

    @property
    def errs(self) -> str:
        return os.path.join(self.root, "Logs", "errs")

    @property
    def frm(self) -> Optional[str]:
        """The install's Bin\\citect.frm, or None when there is none."""
        for b in (self.bin, os.path.join(self.root, "Bin")):
            if b:
                p = _find_file(b, "citect.frm")
                if p:
                    return p
        return None


def locate(root: Optional[str] = None) -> Install:
    """The install the change goes into: the real one (CTG_SCADA_ROOT unset for the lookup)
    or, with ``root``, the fake install at that folder (infra/install.py's fake mode)."""
    from ctgedit.infra import install

    old = os.environ.get(install.ENV)
    try:
        if root:
            os.environ[install.ENV] = os.path.abspath(root)
        else:
            os.environ.pop(install.ENV, None)
        r, user, ini = install.root(), install.user_dir(), install.active_ini()
        bin_dir = install.bin_dir()
    finally:
        if old is None:
            os.environ.pop(install.ENV, None)
        else:
            os.environ[install.ENV] = old
    if not (r and user and ini):
        raise Refused("No Plant SCADA install was found (root %r, User %r, citect.ini %r)." % (r, user, ini))
    if not os.path.isfile(os.path.join(user, "MASTER.DBF")):
        raise Refused("The install's User folder has no MASTER.DBF: %s" % user)
    return Install(
        os.path.abspath(r),
        os.path.abspath(user),
        os.path.abspath(ini),
        os.path.abspath(bin_dir) if bin_dir else None,
    )


def default_backup_root() -> str:
    from ctgedit.infra.paths import data_dir

    return data_dir("fixture_oracle")


def _inside(path: str, folder: str) -> bool:
    p, f = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def running_programs() -> Dict[str, List[int]]:
    """The watched programs that run now: {image: [pid, ...]}."""
    from ctgedit.backend import winui

    out = {}
    for image in WATCHED:
        pids = winui.pids_of(image)
        if pids:
            out[image] = pids
    return out


def stop_studio(log: Callable[[str], None] = _say, timeout: float = 60.0) -> None:
    """Close Plant SCADA Studio (WM_CLOSE to its windows), then end what still runs of it,
    the Graphics Builder and the compiler (taskkill)."""
    from ctgedit.backend import winui

    pids = winui.pids_of("CitectIDE.exe")
    if pids:
        log("Closing Plant SCADA Studio (pid %s)." % ", ".join(map(str, pids)))
        with contextlib.suppress(Exception):
            import win32con
            import win32gui
            import win32process

            def close(h, _):
                with contextlib.suppress(Exception):
                    if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] in pids:
                        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                return True

            win32gui.EnumWindows(close, None)
        deadline = time.time() + timeout
        while time.time() < deadline and winui.pids_of("CitectIDE.exe"):
            time.sleep(1.0)
    for image in WATCHED:
        left = winui.pids_of(image)
        if left:
            log("Ending %s (pid %s)." % (image, ", ".join(map(str, left))))
            for pid in left:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, text=True)
    deadline = time.time() + 20.0
    while time.time() < deadline and any(winui.pids_of(i) for i in WATCHED):
        time.sleep(0.5)


# ---------------------------------------------------------------------------- DBF helpers
def dbf_names(path: str) -> List[str]:
    """The NAME of every live row of a table."""
    from ctgedit.format.projects import read_dbf_rows

    return [r.get("NAME", "") for r in read_dbf_rows(path) if r.get("NAME", "")]


def append_rows(path: str, rows: Sequence[Dict[str, str]]) -> None:
    from ctgedit.format import dbf

    with dbf.Dbf(path) as t:
        t.append(t.encode_many(rows))


def dbf_rows(data: bytes) -> Tuple[List[str], List[Tuple[str, Dict[str, str]]]]:
    """(field names, [(flag, row)]) of a table's bytes: flag ' ' live, '*' deleted."""
    if len(data) < 32:
        return [], []
    nrec, hlen, rlen = struct.unpack_from("<IHH", data, 4)
    fields = []
    pos = 1
    for o in range(32, hlen - 31, 32):
        if data[o] == 0x0D:
            break
        name = data[o : o + 11].split(b"\0")[0].decode("cp1252", "replace")
        ln = data[o + 16]
        fields.append((name, pos, ln))
        pos += ln
    rows = []
    for i in range(nrec):
        rec = data[hlen + i * rlen : hlen + (i + 1) * rlen]
        if len(rec) < rlen:
            break
        rows.append(
            (
                rec[:1].decode("latin-1"),
                {n: rec[o : o + ln].decode("cp1252", "replace").rstrip() for n, o, ln in fields},
            )
        )
    return [n for n, _o, _l in fields], rows


def dbf_diff(before: Optional[bytes], after: bytes) -> dict:
    """The rows a save added or changed in a table: an added row with its fields that are
    not blank (``_flag`` ' ' live, '*' deleted), a changed row as its index, its flags and
    the fields that differ ([before, after])."""
    _f0, old = dbf_rows(before or b"")
    names, new = dbf_rows(after)
    added = [dict({k: v for k, v in r.items() if v}, _flag=f) for f, r in new[len(old) :]]
    changed = []
    for i in range(min(len(old), len(new))):
        (f0, r0), (f1, r1) = old[i], new[i]
        if (f0, r0) != (f1, r1):
            changed.append(
                {
                    "index": i,
                    "flag": [f0, f1],
                    "fields": {k: [r0.get(k, ""), v] for k, v in r1.items() if r0.get(k, "") != v},
                }
            )
    return {"fields": names, "added": added, "changed": changed}


# ---------------------------------------------------------------------------- citect.ini
def ini_set(raw: bytes, section: str, key: str, value: str) -> bytes:
    """``[section] key=value`` in citect.ini bytes (section and key without case; a line
    that is there keeps its spelling; a new one goes at the end of its section)."""
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.split(nl)
    sec, found, end_of, head = None, False, None, None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith(b"[") and s.endswith(b"]"):
            sec = s[1:-1].strip().lower()
            if sec == section.lower().encode():
                head = i
            continue
        if sec != section.lower().encode():
            continue
        if s:
            end_of = i
        if (
            b"=" in ln
            and not s.startswith(b";")
            and ln.split(b"=", 1)[0].strip().lower() == key.lower().encode()
        ):
            lines[i] = ln.split(b"=", 1)[0] + b"=" + value.encode("cp1252")
            found = True
    if not found:
        new = key.encode() + b"=" + value.encode("cp1252")
        if end_of is not None:
            lines.insert(end_of + 1, new)
        elif head is not None:
            lines.insert(head + 1, new)
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += [b"", b"[" + section.encode() + b"]", new, b""]
    return nl.join(lines)


def ini_get(raw: bytes, section: str, key: str) -> Optional[str]:
    sec = None
    for ln in raw.decode("cp1252", "replace").splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip().lower()
        elif (
            sec == section.lower()
            and "=" in s
            and not s.startswith(";")
            and s.split("=", 1)[0].strip().lower() == key.lower()
        ):
            return s.split("=", 1)[1].strip()
    return None


def ini_items(raw: bytes) -> Dict[Tuple[str, str], str]:
    """Every ``[section] key=value`` of citect.ini bytes: {(section, key): value}, section
    and key in lower case (comments and blank lines left out)."""
    out, sec = {}, ""
    for ln in raw.decode("cp1252", "replace").splitlines():
        s = ln.strip()
        if not s or s.startswith(";"):
            continue
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip().lower()
        elif "=" in s:
            k, v = s.split("=", 1)
            out[(sec, k.strip().lower())] = v.strip()
    return out


def master_names(data: bytes) -> List[str]:
    """The NAME of every live row of MASTER.DBF bytes, in lower case, sorted."""
    _f, rows = dbf_rows(data or b"")
    return sorted(r.get("NAME", "").lower() for flag, r in rows if flag == " " and r.get("NAME", ""))


# ============================================================================ the change
def table_problems(source_user: str, frm: str, projects: Sequence[Tuple[str, str]] = PROJECTS) -> List[str]:
    """How the tables the run would install differ from what CtDraw checks."""
    from ctgedit.format import dbfschema as S
    from ctgedit.format.projects import read_dbf_rows

    forms = S.forms_by_table(S.read_frm(frm))
    known = set(S.tables())
    out: List[str] = []
    stale = set()
    for src, tgt in projects:
        folder = os.path.join(source_user, src)
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        files = {
            os.path.splitext(f)[0].lower(): os.path.join(folder, f)
            for f in names
            if f.lower().endswith(".dbf")
        }
        languages = set()
        if "lang" in files:
            languages = {
                os.path.splitext(os.path.basename(r.get("SRC_FILE", "")))[0].lower()
                for r in read_dbf_rows(files["lang"])
            }
        for stem, path in sorted(files.items()):
            label = "%s\\%s" % (tgt, os.path.basename(path))
            form = forms.get(stem)
            if form is not None:
                want = S.form_fields(form)
                out += S.compare(S.read_fields(path), want, label)
                index = S.form_index(form)
                if index:
                    width = next(w for n, _t, w, _d in want if n == index)
                    out += S.index_problems(path, index, width, label)
                if (
                    stem in known
                    and stem not in stale
                    and (S.fields(stem) != want or S.index_field(stem) != index)
                ):
                    stale.add(stem)
                    out.append(
                        "the committed schema's %s is not the form of %s (run "
                        "tools/live/dbf_schema.py --check)" % (stem, frm)
                    )
            elif stem in languages:
                want = S.fields(S.LANGUAGE)
                out += S.compare(S.read_fields(path), want, label)
                index = S.index_field(S.LANGUAGE)
                if index:
                    width = next(w for n, _t, w, _d in want if n == index)
                    out += S.index_problems(path, index, width, label)
    return out


class InstallChange:
    """The reversible change of the install: backup, copies, rows, and restore."""

    def __init__(
        self,
        inst: Install,
        run_dir: str,
        source: str = FIXTURES,
        log: Callable[[str], None] = _say,
        processes: Optional[Callable[[], Dict[str, List[int]]]] = None,
        ini_edits: bool = True,
    ):
        self.inst = inst
        self.run_dir = os.path.abspath(run_dir)
        self.source = os.path.abspath(source)
        self.log = log
        self.processes = processes or (lambda: running_programs())
        self.ini_edits = ini_edits
        self.state_path = os.path.join(self.run_dir, "state.json")
        self.state: dict = {}

    # ------------------------------------------------------------------ what it changes
    def watched_files(self) -> List[str]:
        """The install's files the run may change and the restore puts back: MASTER.DBF and
        every other User\\master.* file, citect.ini, Logs\\errs."""
        out = [os.path.join(self.inst.user, "MASTER.DBF")]
        with contextlib.suppress(OSError):
            for f in sorted(os.listdir(self.inst.user)):
                p = os.path.join(self.inst.user, f)
                if f.lower().startswith("master.") and f.lower() != "master.dbf" and os.path.isfile(p):
                    out.append(p)
        out += [self.inst.ini, self.inst.errs]
        return out

    def master_rows(self) -> List[Dict[str, str]]:
        now = _dt.datetime.now()
        date = "%2d %s %d" % (now.day, now.strftime("%b"), now.year)
        return [
            {
                "NAME": t,
                "TITLE": t,
                "PATH": os.path.join(self.inst.user, t) + "\\",
                "MAJOR_VER": "0000",
                "MINOR_VER": "0000",
                "DATE": date,
                "TIME": now.strftime("%H:%M"),
            }
            for t in TARGETS
        ]

    def ini_changes(self) -> List[Tuple[str, str, str]]:
        if not self.ini_edits:
            return []
        out = [
            ("CtEdit", "LastDatabase", "FxMain"),
            ("CtEdit", "LastDatabasePath", os.path.join(self.inst.user, "FxMain")),
        ]
        raw = _read(self.inst.ini) or b""
        if (ini_get(raw, "Animator", "FastDisplay") or "1").strip() == "0":
            out.append(("Animator", "FastDisplay", "1"))
        return out

    def plan(self) -> List[str]:
        """Every change, in order, as text (the dry run prints it)."""
        lines = [
            "Install: %s (User %s, citect.ini %s)" % (self.inst.root, self.inst.user, self.inst.ini),
            "Run folder (backup, state, logs): %s" % self.run_dir,
        ]
        frm = self.inst.frm
        if not frm:
            lines.append("Check the tables: REFUSED, the install has no Bin\\citect.frm")
        else:
            bad = table_problems(os.path.join(self.source, "User"), frm)
            lines.append(
                "Check every table against %s: %s"
                % (frm, "%d differences, the run is refused" % len(bad) if bad else "all match")
            )
            lines += ["  " + b for b in bad[:20]]
        for p in self.watched_files():
            lines.append(
                "Back up %s (%s)"
                % (
                    p,
                    "sha1 %s" % file_sha1(p)
                    if os.path.isfile(p)
                    else "absent: a file made by the run is removed",
                )
            )
        for src, tgt in PROJECTS:
            lines.append(
                "Copy %s to %s" % (os.path.join(self.source, "User", src), os.path.join(self.inst.user, tgt))
            )
        for _src, tgt in PROJECTS:
            if tgt not in INCLUDE_ROWS:
                lines.append("Append to %s\\include.DBF: %s" % (tgt, ", ".join(INCLUDE_ROWS)))
        for r in self.master_rows():
            lines.append("Append to MASTER.DBF: NAME %s, PATH %s" % (r["NAME"], r["PATH"]))
        for sec, key, val in self.ini_changes():
            lines.append("Set citect.ini [%s] %s=%s" % (sec, key, val))
        lines.append(
            "After the capture: stop Studio, delete the %d folders, put the backed "
            "up files back and check their sha1." % len(TARGETS)
        )
        return lines

    # ------------------------------------------------------------------ checks
    def preflight(self) -> None:
        """Refuse before anything changes."""
        if not os.path.isdir(os.path.join(self.source, "User")):
            raise Refused("The fixtures are missing: %s (run tools/make_fixtures.py)." % self.source)
        running = self.processes()
        if running:
            raise Refused(
                "Close these first: %s. Plant SCADA Studio must start after the "
                "projects are registered." % ", ".join(sorted(running))
            )
        taken = []
        registered = {n.lower() for n in dbf_names(os.path.join(self.inst.user, "MASTER.DBF"))}
        folders = {f.lower() for f in os.listdir(self.inst.user)}
        for t in TARGETS:
            if t.lower() in registered:
                taken.append("%s (in MASTER.DBF)" % t)
            elif t.lower() in folders:
                taken.append("%s (a folder in User)" % t)
        if taken:
            raise Refused(
                "These project names are in the install already: %s. Nothing was changed." % ", ".join(taken)
            )
        for root in (self.inst.root, self.inst.user):
            if _inside(self.run_dir, root):
                raise Refused("The backup folder must be outside the install: %s" % self.run_dir)
        free = shutil.disk_usage(os.path.dirname(self.inst.user)).free
        if free < 300 << 20:
            raise Refused("Less than 300 MB free on the install's drive (%d MB)." % (free >> 20))
        self.check_tables()
        missing = project_file_problems(os.path.join(self.source, "User"))
        if missing:
            raise Refused(
                "These projects lack a file Studio or the Graphics Builder needs "
                "before it works on a project:\n  %s\nNothing was changed. Write "
                "the fixtures again (tools/make_fixtures.py)." % "\n  ".join(missing)
            )

    def check_tables(self) -> None:
        """Refuse when a table the run would install is not in the layout of the install's
        citect.frm (:func:`table_problems`), or the install has no citect.frm."""
        frm = self.inst.frm
        if not frm:
            raise Refused(
                "The install has no Bin\\citect.frm (looked in %s): the tables "
                "cannot be checked. Nothing was changed."
                % (self.inst.bin or os.path.join(self.inst.root, "Bin"))
            )
        bad = table_problems(os.path.join(self.source, "User"), frm)
        if bad:
            more = len(bad) - 20
            raise Refused(
                "These tables do not match the forms of %s (CtDraw would stop "
                "with 'database field size mismatch'):\n  %s%s\nNothing was "
                "changed. Write the fixtures again (tools/make_fixtures.py)."
                % (frm, "\n  ".join(bad[:20]), "\n  and %d more" % more if more > 0 else "")
            )

    # ------------------------------------------------------------------ the state file
    def _save_state(self) -> None:
        write_json(self.state_path, self.state)

    def _snapshot(self) -> dict:
        """Name, size and mtime of the top level of User and Config (to report what else
        changed while the run went on)."""
        out = {}
        for folder in (self.inst.user, os.path.dirname(self.inst.ini)):
            with contextlib.suppress(OSError):
                for f in sorted(os.listdir(folder)):
                    p = os.path.join(folder, f)
                    if f.lower() in {t.lower() for t in TARGETS}:
                        continue
                    if os.path.isdir(p):
                        out[p] = "folder"
                    else:
                        st = os.stat(p)
                        out[p] = "%d %d" % (st.st_size, int(st.st_mtime))
        return out

    # ------------------------------------------------------------------ apply
    def apply(self) -> None:
        """Back up, copy, append. ``state.json`` records each step before it is made, so
        :meth:`restore` (or the ``restore`` subcommand after a crash) undoes what was done."""
        self.preflight()
        os.makedirs(os.path.join(self.run_dir, "backup"), exist_ok=True)
        run_id = "%s-%d" % (os.path.basename(self.run_dir), os.getpid())
        self.state = {
            "tool": TOOL,
            "version": TOOL_VERSION,
            "run_id": run_id,
            "install": {"root": self.inst.root, "user": self.inst.user, "ini": self.inst.ini},
            "started": _dt.datetime.now().isoformat(timespec="seconds"),
            "phase": "backing up",
            "files": [],
            "projects": [],
            "snapshot": self._snapshot(),
        }
        self._save_state()
        for k, p in enumerate(self.watched_files()):
            ent = {"path": p, "existed": os.path.isfile(p)}
            if ent["existed"]:
                data = _read(p)
                bak = os.path.join(self.run_dir, "backup", "%02d_%s" % (k, os.path.basename(p)))
                _write(bak, data)
                if file_sha1(bak) != sha1(data):
                    raise Refused("The backup of %s does not read back." % p)
                ent.update(backup=bak, sha1=sha1(data), size=len(data))
            self.state["files"].append(ent)
            self.log("Backed up %s" % p if ent["existed"] else "Noted %s (absent)" % p)
        self.state["phase"] = "copying"
        self._save_state()
        for src, tgt in PROJECTS:
            dst = os.path.join(self.inst.user, tgt)
            self.state["projects"].append({"name": tgt, "folder": dst, "source": src})
            self._save_state()
            os.makedirs(dst)
            _write(os.path.join(dst, MARKER), run_id.encode())
            copy_project(os.path.join(self.source, "User", src), dst)
            self.log("Copied %s to %s" % (src, dst))
        for _src, tgt in PROJECTS:
            if tgt in INCLUDE_ROWS:
                continue
            inc = _find_file(os.path.join(self.inst.user, tgt), "include.DBF")
            if inc is None:
                raise Refused("%s has no include.DBF." % tgt)
            append_rows(inc, [{"NAME": n, "COMMENT": "fixture oracle"} for n in INCLUDE_ROWS])
        self.log(
            "Appended %s to the include.DBF of %d projects."
            % (" and ".join(INCLUDE_ROWS), len(TARGETS) - len(INCLUDE_ROWS))
        )
        self.state["phase"] = "registering"
        self._save_state()
        append_rows(os.path.join(self.inst.user, "MASTER.DBF"), self.master_rows())
        self.log("Appended %d rows to MASTER.DBF." % len(TARGETS))
        changes = self.ini_changes()
        self.state["ini_keys"] = [[sec, key] for sec, key, _v in changes]
        self._save_state()
        if changes:
            raw = _read(self.inst.ini) or b""
            for sec, key, val in changes:
                raw = ini_set(raw, sec, key, val)
                self.log("citect.ini [%s] %s=%s" % (sec, key, val))
            _write(self.inst.ini, raw)
        self.state["phase"] = "installed"
        self._save_state()

    # ------------------------------------------------------------------ restore
    def note_seen(self) -> None:
        """Record the sha1 of each backed up file as the run left it (after each unit and
        before the restore): a file still at that sha1 has not been changed by anyone else,
        so the restore may put the backup back without a check."""
        if not self.state:
            return
        self.state["seen"] = {e["path"]: file_sha1(e["path"]) for e in self.state.get("files", [])}
        self._save_state()

    def _stale(self, path: str, now: bytes, backup: bytes) -> Optional[str]:
        """Why ``path`` (``now``) must not be overwritten with ``backup``: it changed after
        the run in a way the run does not explain. None when the backup may go back."""
        if sha1(now) == (self.state.get("seen") or {}).get(path):
            return None
        base = os.path.basename(path).lower()
        if base == "master.dbf":
            targets = {t.lower() for t in TARGETS}
            have = [n for n in master_names(now) if n not in targets]
            want = master_names(backup)
            if have != want:
                return "its projects are not the backup's plus the Fx ones (more: %s; fewer: %s)" % (
                    ", ".join(sorted(set(have) - set(want))) or "none",
                    ", ".join(sorted(set(want) - set(have))) or "none",
                )
            return None
        if os.path.normcase(path) == os.path.normcase(self.inst.ini):
            keys = {(sec.lower(), key.lower()) for sec, key in (self.state.get("ini_keys") or RUN_INI_KEYS)}
            keys |= set(INI_SESSION_KEYS)

            def mine(k):
                return k in keys or k[0].startswith(INI_SESSION_SECTIONS) or "windowposition" in k[0]

            a, b = ini_items(now), ini_items(backup)
            diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k) and not mine(k))
            if diff:
                return "these lines are not the run's: %s" % ", ".join("[%s] %s" % k for k in diff[:8])
            return None
        return None

    def _restore_files(self, force: bool) -> Tuple[bool, List[str]]:
        """Put the backed up files back (a file changed after the run only with ``force``),
        remove what the run made; then check each sha1. Returns (all good, report)."""
        ok = True
        user = self.state["install"]["user"]
        refused = set()
        master_refused = False
        for ent in self.state.get("files", []):
            p = ent["path"]
            if ent.get("existed"):
                data = _read(ent["backup"])
                if data is None or sha1(data) != ent["sha1"]:
                    self.log("The backup of %s is damaged: %s" % (p, ent["backup"]))
                    ok = False
                    refused.add(p)
                    continue
                now = _read(p)
                if now == data:
                    continue
                base = os.path.basename(p).lower()
                why = None
                if base.startswith("master.") and base != "master.dbf" and master_refused:
                    why = "MASTER.DBF was not put back"
                elif not force and now is not None:
                    why = self._stale(p, now, data)
                if why:
                    self.log(
                        "NOT restored, it changed after the run (%s): %s. Check it, then "
                        "run restore again with --force to put the backup back." % (why, p)
                    )
                    ok = False
                    refused.add(p)
                    master_refused = master_refused or base == "master.dbf"
                    continue
                _write(p, data)
            elif os.path.exists(p):
                os.remove(p)
                self.log("Removed %s (the run made it)." % p)
        # a master.* file (an index) the run made: only with MASTER.DBF put back
        if not master_refused:
            known = {os.path.normcase(e["path"]) for e in self.state.get("files", [])}
            known |= {os.path.normcase(p) for p in self.state.get("snapshot", {})}
            with contextlib.suppress(OSError):
                for f in os.listdir(user):
                    p = os.path.join(user, f)
                    if (
                        f.lower().startswith("master.")
                        and os.path.isfile(p)
                        and os.path.normcase(p) not in known
                    ):
                        os.remove(p)
                        self.log("Removed %s (the run made it)." % p)
        report = []
        for ent in self.state.get("files", []):
            p = ent["path"]
            if p in refused:
                report.append("BAD %s: not restored" % p)
            elif ent.get("existed"):
                got = file_sha1(p)
                good = got == ent["sha1"]
                report.append("%s %s: sha1 %s" % ("OK " if good else "BAD", p, got))
                ok = ok and good
            else:
                good = not os.path.exists(p)
                report.append("%s %s: absent" % ("OK " if good else "BAD", p))
                ok = ok and good
        return ok, report

    def restore(self, stop: bool = True, force: bool = False) -> bool:
        """Undo :meth:`apply` from the state file; True when every check passed. The files
        are put back once (``files_restored`` in the state): a later restore only retries
        the folders. ``force``: put a backup back even over a file changed after the run."""
        if not self.state:
            self.state = json.loads(_read(self.state_path).decode("utf-8"))
        if self.state.get("phase") == "restored":
            self.log("This run was restored already (%s)." % self.state_path)
            return True
        running = self.processes()
        if running:
            if not stop:
                raise Refused("Close these first: %s (or pass --stop-studio)." % ", ".join(sorted(running)))
            stop_studio(self.log)
            if self.processes():
                raise Refused("These still run: %s." % ", ".join(sorted(self.processes())))
        ok = True
        run_id = self.state.get("run_id", "")
        user = self.state["install"]["user"]
        for proj in self.state.get("projects", []):
            folder = proj["folder"]
            if (
                os.path.normcase(os.path.dirname(folder)) != os.path.normcase(user)
                or proj["name"] not in TARGETS
            ):
                self.log("NOT deleted (not a target folder): %s" % folder)
                ok = False
                continue
            if not os.path.isdir(folder):
                continue
            marker = _read(os.path.join(folder, MARKER))
            if marker is None and not os.listdir(folder):
                os.rmdir(folder)  # (made, crashed before its marker)
                continue
            if marker != run_id.encode():
                self.log("NOT deleted (no marker of this run): %s" % folder)
                ok = False
                continue
            if not _rmtree(folder, self.log):
                ok = False
        if self.state.get("files_restored"):
            self.log(
                "The files were put back on %s: only the folders are checked." % self.state["files_restored"]
            )
            report = []
        else:
            files_ok, report = self._restore_files(force)
            if files_ok:
                self.state["files_restored"] = _dt.datetime.now().isoformat(timespec="seconds")
                self._save_state()
            ok = ok and files_ok
        for proj in self.state.get("projects", []):
            good = not os.path.exists(proj["folder"])
            report.append("%s %s: deleted" % ("OK " if good else "BAD", proj["folder"]))
            ok = ok and good
        for line in report:
            self.log(line)
        now = self._snapshot()
        before = self.state.get("snapshot", {})
        watched = {os.path.normcase(e["path"]) for e in self.state.get("files", [])}
        other = sorted(
            p
            for p in set(before) | set(now)
            if before.get(p) != now.get(p) and os.path.normcase(p) not in watched
        )
        for p in other:
            self.log("Note: changed during the run (not restored by this tool): %s" % p)
        if ok:
            self.state["phase"] = "restored"
            self.state["restored"] = _dt.datetime.now().isoformat(timespec="seconds")
            self._save_state()
            self.log(
                "Restore verified: %d files byte for byte, %d folders deleted."
                % (len(self.state.get("files", [])), len(self.state.get("projects", [])))
            )
        else:
            self.log(
                "RESTORE INCOMPLETE. The state is in %s; fix what is listed above and run "
                "'fixture_oracle.py restore --backup %s' again." % (self.state_path, self.run_dir)
            )
        return ok


def copy_project(src: str, dst: str) -> None:
    """Copy a fixture project's files into its (made, marked) install folder; a copy under
    another name (Include as FxInclude) takes that name in its PROJECT.CIT too."""
    shutil.copytree(src, dst, dirs_exist_ok=True)
    name = os.path.basename(dst)
    if name != os.path.basename(src):
        from ctgedit.io import projectcit

        path = projectcit.find(dst)
        if path is None or not projectcit.patch_streams(path, {"Name": name.encode("cp1252")}):
            raise Refused("Could not name the copy %s in its PROJECT.CIT." % dst)


#: the files every project the run installs needs (any case), and why: what Studio or the
#: Graphics Builder does without one
PROJECT_FILES = (
    (
        "GLOBAL.CTL",
        "CtDraw dates the project by it: 'Cannot convert ... You MUST use version 5.31 or greater'",
    ),
    ("units.DBF", "Studio's tag refresh: 'Unable to Refresh Tags, the file could not be found'"),
    (
        "pages.DBF",
        "a library save reads it in every project of the chain: 'Form: Pages, "
        "Cannot open file' on every save",
    ),
    ("pgdynobj.DBF", "and the other pg tables: 'Form: Dynamic Object, Cannot open file'"),
    ("fonts.DBF", "a page save reads it: 'Form: Fonts, Cannot open file'"),
    ("fcgpage.DBF", "a genie save writes it: 'Form: Force Compile Genie Pages, Cannot open file'"),
    ("pglibobj.DBF", "a genie save writes it: 'Form: Library Objects, Cannot open file'"),
    (
        "variable.DBF",
        "a genie open reads it: 'Form: Variable Tags, Cannot open file', and PageOpenEx of the genie fails",
    ),
)


def project_file_problems(source_user: str, projects: Sequence[Tuple[str, str]] = PROJECTS) -> List[str]:
    """What the projects the run would install lack, one line each: a PROJECT.CIT whose
    Name stream can take the copy's name (Studio cannot locate a project without one and
    never starts the Graphics Builder), and each of :data:`PROJECT_FILES`."""
    from ctgedit.io import projectcit

    out = []
    for src, tgt in projects:
        label = src if src == tgt else "%s (as %s)" % (src, tgt)
        folder = os.path.join(source_user, src)
        path = projectcit.find(folder)
        streams = projectcit.read_streams(path) if path else {}
        # the Name stream is patched in place: the new name must fit its 64-byte sectors
        room = 64 * max(1, -(-len(streams.get("Name", b"")) // 64))
        if "Name" not in streams or len(tgt) > room:
            out.append("%s: no usable PROJECT.CIT (Studio cannot locate the project)" % label)
        try:
            names = {f.lower() for f in os.listdir(folder)}
        except OSError:
            names = set()
        out += ["%s: no %s (%s)" % (label, f, why) for f, why in PROJECT_FILES if f.lower() not in names]
    return out


def _rmtree(folder: str, log) -> bool:
    """Delete a run's folder with its marker LAST: everything else first (each file with a
    retry after a chmod), then the marker, then the folder. A delete that fails part way
    (a file held open) keeps the marker, so the next restore still knows the folder as
    this run's and can finish the job."""

    def again(fn, path, _exc):
        with contextlib.suppress(OSError):
            os.chmod(path, 0o666)
            fn(path)

    def remove(path):
        try:
            os.remove(path)
        except OSError:
            again(os.remove, path, None)

    for attempt in range(RMTREE_TRIES):
        try:
            for e in list(os.scandir(folder)):
                if e.name.lower() == MARKER.lower():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if sys.version_info >= (3, 12):
                        shutil.rmtree(e.path, onexc=again)
                    else:
                        shutil.rmtree(e.path, onerror=again)
                else:
                    remove(e.path)
            left = [n for n in os.listdir(folder) if n.lower() != MARKER.lower()]
            if not left:
                marker = os.path.join(folder, MARKER)
                if os.path.exists(marker):
                    remove(marker)
                os.rmdir(folder)
            else:
                log(
                    "Deleting %s: %d left (try %d): %s"
                    % (folder, len(left), attempt + 1, ", ".join(sorted(left)[:5]))
                )
        except OSError as e:
            log("Deleting %s: %s (try %d)" % (folder, e, attempt + 1))
        if not os.path.exists(folder):
            log("Deleted %s" % folder)
            return True
        if attempt + 1 < RMTREE_TRIES:
            time.sleep(RMTREE_PAUSE)
    log("Could not delete %s (its marker stays: run restore again once the files are free)" % folder)
    return False


def open_runs(backup_root: str) -> List[str]:
    """The run folders under ``backup_root`` whose state is not 'restored', newest first."""
    out = []
    with contextlib.suppress(OSError):
        for d in sorted(os.listdir(backup_root), reverse=True):
            st = _read(os.path.join(backup_root, d, "state.json"))
            if st is None:
                continue
            with contextlib.suppress(ValueError):
                if json.loads(st.decode("utf-8")).get("phase") != "restored":
                    out.append(os.path.join(backup_root, d))
    return out


def change_from_state(run_dir: str, log=_say, processes=None) -> InstallChange:
    st = json.loads(_read(os.path.join(run_dir, "state.json")).decode("utf-8"))
    i = st["install"]
    ch = InstallChange(Install(i["root"], i["user"], i["ini"]), run_dir, log=log, processes=processes)
    ch.state = st
    return ch


# ============================================================================ the steps
@dataclass
class Unit:
    step: str
    name: str
    what: str
    feeds: List[str]
    run: Callable[["Oracle", "Unit"], None]
    sources: List[str] = field(default_factory=list)  # fixture files (User relative)
    data: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return "%s:%s" % (self.step, self.name)


@dataclass
class Step:
    name: str
    what: str
    feeds: List[str]
    units: Callable[[], List[Unit]]
    #: its units drive the Graphics Builder (the step then starts in a fresh one)
    builder: bool = True


def _t(*names) -> List[str]:
    return ["tests/%s.py" % n for n in names]


#: every page capture feeds these
PAGE_COMMON = _t(
    "test_foundation",
    "test_ctf",
    "test_perf_parser",
    "test_bag_synthesis",
    "test_engine_rules",
    "test_pgregister",
)
#: page -> the tests that use its capture besides PAGE_COMMON
PAGE_FEEDS: Dict[str, List[str]] = {
    "FxShapes": _t(
        "test_render_parity",
        "test_appearance_kinds",
        "test_editor_parity",
        "test_node_editing",
        "test_symbol_resolution",
        "test_display_value",
    ),
    "FxLegacy": _t("test_special_objects"),
    "FxAnim": _t(
        "test_display_value", "test_access_metadata_ai", "test_sim_pagemodel", "test_format_compliance"
    ),
    "FxSymbols": _t("test_symbol_workflows", "test_symbol_sets"),
    "FxSymbols2": _t("test_bitmaps", "test_symbol_sets"),
    "FxGenies": _t(
        "test_genie_recompose_art", "test_nested_recompose", "test_genie_write_rules", "test_display_value"
    ),
    "FxParts": _t(
        "test_elements",
        "test_genie_recompose_art",
        "test_format_compliance",
        "test_owner_graft",
        "test_display_value",
    ),
    "FxArrange": _t("test_arrange_correctness", "test_symbol_sets"),
    "FxInputs": _t("test_input_records"),
    "FxButtons": _t("test_input_records", "test_display_value"),
    "FxTextKinds": _t(
        "test_mixer_visuals",
        "test_render_details",
        "test_transforms",
        "test_format_compliance",
        "test_display_value",
    ),
    "FxColours": _t("test_fill_tab", "test_mixing_visuals", "test_page_properties", "test_render_parity"),
    "FxMembers": _t("test_owner_graft"),
    "FxMany": _t("test_save_rules", "test_access_metadata_ai", "test_node_editing", "test_display_value"),
    "FxCicode": _t("test_anpoint_markers"),
    "FxMotion": _t("test_motion_tabs"),
    "FxActiveX": _t("test_special_objects"),
    "FxStyles": _t("test_format_compliance"),
    "FxAlarms": _t("test_access_metadata_ai", "test_render_parity", "test_display_value"),
    "FxWork": _t("test_anpoint_markers"),
    "FxWide": _t("test_anpoint_markers"),
    "FxUnderlay": _t("test_render_parity"),
    "FxThumb": _t("test_save_sidecars"),
    "FxTplLast": _t("test_template_zorder", "test_templates"),
    "FxAssoc": _t("test_super_genies"),
    "!fx_popup": _t("test_super_genies"),
    "!fx_detail": _t("test_super_genies"),
    "FxMaster": _t("test_page_properties"),
    "FxComposite": _t("test_composites", "test_symbol_resolution", "test_find_replace"),
    "FxComposites": _t("test_composites", "test_symbol_resolution", "test_find_replace"),
    "FxMissing": _t("test_placeholder_genies"),
    "FxPlaceholder": _t("test_placeholder_genies"),
    "FxReturned": _t("test_placeholder_genies"),
    "!FxSystem": _t("test_settings_parity"),
    "FxBig": _t("test_perf_ui", "test_display_value"),
    "FxOld9": _t("test_legacy_upgrade"),
    "FxOld10": _t("test_legacy_upgrade"),
    "FxOld11": _t("test_legacy_upgrade"),
    "FxTplArrows": _t("test_template_zorder"),
    "FxLegacyNames": _t("test_access_metadata_ai"),
    "FxAnPoints": _t("test_anpoint_markers"),
    "FxNoAnNames": _t("test_save_rules"),
    "FxScaled": _t("test_session_soak"),
    "FxParity": _t("test_editor_parity"),
    "FxFreehand": _t("test_render_parity"),
    "FxLegacy2": _t("test_render_parity"),
}
#: pages whose canvas is shot too (the render facts the inventory asks for)
SHOT_PAGES = {
    "FxShapes",
    "FxLegacy",
    "FxAnim",
    "FxSymbols",
    "FxSymbols2",
    "FxGenies",
    "FxParts",
    "FxTextKinds",
    "FxColours",
    "FxMembers",
    "FxCicode",
    "FxWide",
    "FxStyles",
    "FxUnderlay",
    "FxComposites",
    "FxTplLast",
    "FxAnPoints",
}
#: the projects whose pages are captured
PAGE_PROJECTS = ("FxMain", "FxOther", "FxSim", "FxSa")

#: library (file name, lower case) -> tests besides ELEMENT_COMMON
ELEMENT_COMMON = _t("test_elements", "test_perf_parser", "test_bag_synthesis")
LIBRARY_FEEDS: Dict[str, List[str]] = {
    "fxnest.ctm": _t("test_genie_nesting", "test_nested_recompose"),
    "fxsuper.ctm": _t("test_super_genies"),
    "fxgenie.ctm": _t("test_genie_write_rules", "test_genie_dialog"),
    "fxparts.ctm": _t(
        "test_format_compliance", "test_access_metadata_ai", "test_genie_dialog", "test_genie_recompose_art"
    ),
    "fxmany.ctl": _t("test_library_growth"),
    "fxsym.ctl": _t("test_bitmaps", "test_symbol_workflows"),
    "fxmore.ctl": _t("test_bitmaps", "test_symbol_workflows", "test_symbol_sets"),
    "fxprio.ctl": _t("test_access_metadata_ai"),
    "fxprio.ctm": _t("test_access_metadata_ai", "test_render_parity"),
    "fxbmpgrp.ctl": _t("test_bag_synthesis"),
    "fx_style.ctt": _t("test_templates", "test_template_place"),
    "fx_alt.ctt": _t("test_templates", "test_template_place"),
    "fx_hd.ctt": _t("test_templates"),
    "fx_sa.ctt": _t("test_templates"),
}
#: (library, element) whose element window is shot
ELEMENT_SHOTS = {("fxgenie.ctm", "fx_pump"), ("fxparts.ctm", "fx_mixed"), ("fxsym.ctl", "fx_box")}
LIBRARY_EXTS = (".ctl", ".ctm", ".ctt")


def links_a_genie(lf) -> bool:
    """Whether an element of the library (a LibraryFile) nests a genie: its block names a
    ``<library>.<element>.<ctm`` (the bytes only; the capture process parses no records)."""
    return any(b".<ctm" in lf.block_data(lf.find(n)).lower() for n in lf.element_names())


def linked_elements(data: bytes, opened: Iterable[Tuple[str, str]]) -> List[str]:
    """The elements of ``opened`` ((library, element) in lower case) that ``data`` (a page
    or an element block) names as ``<library>.<element>.<``: those CtDraw would compose
    from the form it holds in memory."""
    low = data.lower()
    return sorted("%s.%s" % k for k in set(opened) if ("%s.%s.<" % k).encode("cp1252", "replace") in low)


def page_units() -> List[Unit]:
    out = []
    for proj in PAGE_PROJECTS:
        src = SOURCE_OF[proj]
        folder = os.path.join(FIXTURES, "User", src)
        for f in sorted(os.listdir(folder), key=str.lower):
            if not f.lower().endswith(".ctg"):
                continue
            page = f[:-4]
            feeds = PAGE_COMMON + PAGE_FEEDS.get(page, [])
            if page.startswith(("FxTpl", "FxHd")):
                feeds = feeds + _t("test_templates", "test_template_place")
            if proj == "FxSim":
                feeds = feeds + _t("test_sim_pagemodel")
            if proj == "FxOther":
                feeds = feeds + _t("test_symbol_resolution")
            if proj == "FxSa":
                feeds = feeds + _t("test_templates", "test_sa_workspace")
            out.append(
                Unit(
                    "pages",
                    "%s/%s" % (proj, page),
                    "CtDraw's PageOpen + PageSave of %s\\%s%s."
                    % (proj, page, ", and its canvas" if page in SHOT_PAGES else ""),
                    sorted(set(feeds)),
                    _run_page,
                    sources=["%s/%s" % (src, f)],
                    data={"project": proj, "page": page, "shot": page in SHOT_PAGES},
                )
            )
    return out


#: the element the append unit opens and the name CtDraw saves it under (PageSaveAsEx)
APPEND_FROM = ("FxLib", "fxmany.ctl", "fx_last_big")
APPEND_AS = "fx_appended"


def element_units() -> List[Unit]:
    from ctgedit.format.element import LibraryFile

    out = []
    for src, proj in PROJECTS:
        folder = os.path.join(FIXTURES, "User", src)
        for f in sorted(os.listdir(folder), key=str.lower):
            ext = os.path.splitext(f)[1].lower()
            if ext not in LIBRARY_EXTS:
                continue
            lf = LibraryFile.open(os.path.join(folder, f))
            names = lf.element_names()
            if not names:
                continue
            nested = links_a_genie(lf)
            out.append(
                Unit(
                    "elements",
                    "%s/%s" % (proj, f),
                    "CtDraw's open + save of each element of %s\\%s (%d), in order, "
                    "and the library after them%s."
                    % (
                        proj,
                        f,
                        len(names),
                        ", in a fresh Graphics Builder (it nests genies)" if nested else "",
                    ),
                    sorted(set(ELEMENT_COMMON + LIBRARY_FEEDS.get(f.lower(), []))),
                    _run_library,
                    sources=["%s/%s" % (src, f)],
                    data={"project": proj, "library": f, "elements": names, "fresh": nested},
                )
            )
    proj, lib, el = APPEND_FROM
    out.append(
        Unit(
            "elements",
            "append",
            "CtDraw's PageSaveAsEx of %s.%s as a new element "
            "%s of the grown %s\\%s: the library after the append."
            % (os.path.splitext(lib)[0], el, APPEND_AS, proj, lib),
            _t("test_library_growth", "test_elements"),
            _run_append,
            sources=["%s/%s" % (SOURCE_OF[proj], lib)],
            data={"project": proj, "library": lib, "element": el, "as": APPEND_AS},
        )
    )
    return out


#: (id, page, the selected named roots, the operations, tests). An operation: 'R' rotate,
#: 'H' / 'V' mirror, 'S' save, 'O' save + close + open, ('at', x, y) PositionAt of the
#: selection, ('select', names) a new selection. A PageSave ends every sequence.
TURNS: Tuple[tuple, ...] = (
    (
        "T01_point_turn",
        "FxArrange",
        ["FxPointGenie"],
        ["R"],
        _t("test_turn_memory", "test_arrange_correctness"),
    ),
    (
        "T02_point_two_turns",
        "FxArrange",
        ["FxPointGenie"],
        ["R", "R"],
        _t("test_turn_memory", "test_arrange_correctness"),
    ),
    (
        "T03_point_text_turn",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["R"],
        _t("test_turn_memory", "test_save_rules", "test_arrange_correctness"),
    ),
    (
        "T04_point_text_four_turns",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["R", "R", "R", "R"],
        _t("test_turn_memory"),
    ),
    (
        "T05_point_text_turn_save_turn",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["R", "S", "R"],
        _t("test_turn_memory"),
    ),
    (
        "T06_point_text_turn_move_turn",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["R", ("select", ["FxLoneText"]), ("at", 100, 100), ("select", ["FxPointGenie", "FxLoneText"]), "R"],
        _t("test_turn_memory"),
    ),
    (
        "T07_three_turns_reopen_turn",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["R", "R", "R", "O", "R"],
        _t("test_turn_memory"),
    ),
    (
        "T08_mirror_two_turns",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["H", "R", "R"],
        _t("test_turn_memory"),
    ),
    (
        "T09_text_mirror_move",
        "FxArrange",
        ["FxLoneText"],
        ["H", ("at", 200, 200)],
        _t("test_turn_memory", "test_arrange_correctness", "test_transforms"),
    ),
    ("T10_text_with_key_turn", "FxArrange", ["FxKeyText"], ["R"], _t("test_turn_memory")),
    (
        "T11_big_group_turn",
        "FxArrange",
        ["FxBigGroup"],
        ["R"],
        _t("test_arrange_correctness", "test_turn_memory"),
    ),
    (
        "T12_carrier_turn",
        "FxArrange",
        ["FxCarrier", "FxSameSize"],
        ["R"],
        _t("test_arrange_correctness", "test_turn_memory"),
    ),
    (
        "T13_symbol_set_turn",
        "FxArrange",
        ["FxTripleSet"],
        ["R"],
        _t("test_arrange_correctness", "test_turn_memory"),
    ),
    ("T14_symbol_turn", "FxMotion", ["FxSymRotate"], ["R"], _t("test_transforms", "test_turn_memory")),
    (
        "T15_symbol_line_turn",
        "FxMotion",
        ["FxSymRotate", "FxNeedle1"],
        ["R"],
        _t("test_transforms", "test_turn_memory"),
    ),
    (
        "T16_stretched_symbol_mirror",
        "FxMotion",
        ["FxSymStretched"],
        ["H"],
        _t("test_transforms", "test_turn_memory"),
    ),
    (
        "T17_full_ellipse_turn",
        "FxShapes",
        ["FxEllipse"],
        ["R"],
        _t("test_arrange_correctness", "test_turn_memory"),
    ),
    (
        "T18_pie_turn",
        "FxShapes",
        ["FxPie"],
        ["R"],
        _t("test_arrange_correctness", "test_appearance_kinds", "test_turn_memory"),
    ),
    ("T19_line_turn_mirror", "FxShapes", ["FxLine"], ["R", "H"], _t("test_turn_memory")),
    (
        "T20_gradient_mirror",
        "FxShapes",
        ["FxGradient"],
        ["H"],
        _t("test_appearance_kinds", "test_turn_memory"),
    ),
    ("T21_composite_turn", "FxComposites", ["FxComp1"], ["R"], _t("test_composites", "test_turn_memory")),
    ("T22_composite_mirror", "FxComposites", ["FxComp1"], ["H"], _t("test_composites", "test_turn_memory")),
    ("T23_text_turn", "FxTextKinds", ["FxTk1"], ["R"], _t("test_transforms", "test_turn_memory")),
    ("T24_cicode_point_turn", "FxCicode", ["FxCicodePoint"], ["R", "R"], _t("test_turn_memory")),
    (
        "T25_group_symbol_turn",
        "FxSymbols",
        ["FxGroupSymbol"],
        ["R"],
        _t("test_symbol_workflows", "test_turn_memory"),
    ),
    (
        "T26_point_text_two_turns",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["R", "R"],
        _t("test_turn_memory"),
    ),
    # (the genie alone turns about a whole-pixel centre: equal to T02, a reopen changes nothing)
    ("T27_point_turn_reopen_turn", "FxArrange", ["FxPointGenie"], ["R", "O", "R"], _t("test_turn_memory")),
    # (a reopen keeps a genie member's half pixel, as T05's save does: the first turn about
    # (677.5, 148) leaves the points on half pixels, the second puts them at y 196, not 195)
    (
        "T28_point_text_turn_reopen_turn",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["R", "O", "R"],
        _t("test_turn_memory"),
    ),
    # (CtDraw's save keeps a top-level genie's and a mirrored text's Local in memory, zero
    # signs and all: T08's records, C +0.0)
    (
        "T29_point_text_mirror_save_two_turns",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["H", "S", "R", "R"],
        _t("test_turn_memory"),
    ),
    # (a genie nested in a plain group: the turns go onto the group's own Local and the
    # save composes it onto the genie's; the capture gives (1, +0.0, +0.0, -1)
    # mode 0x27, where turning the genie's own Local gives B -0.0)
    (
        "T30_genie_in_group_mirror_save_two_turns",
        "FxMembers",
        ["FxGenieInGroup"],
        ["H", "S", "R", "R"],
        _t("test_turn_memory"),
    ),
    # (the same rule across a save: the save composes the group's first turn onto the
    # genie (mode 0x15) and the second turn's group Local onto that, the mode bytes ORed:
    # ctgedit gives (-1, +0.0, +0.0, -1) mode 0x15; turning the genie's own Local would
    # give (-1, -0.0, -0.0, -1) mode 0x25)
    (
        "T31_genie_in_group_turn_save_turn",
        "FxMembers",
        ["FxGenieInGroup"],
        ["R", "S", "R"],
        _t("test_turn_memory"),
    ),
    # (a mirrored text in a plain group, FxPair's FxPairB: the group rule holds for a text
    # too, (1, +0.0, +0.0, -1) mode 0x27 on the text's Local; turning the text's own Local
    # would give B -0.0)
    ("T32_pair_mirror_save_two_turns", "FxMembers", ["FxPair"], ["H", "S", "R", "R"], _t("test_turn_memory")),
    # (the group rule with no save: the group's Local holds the mirror and both turns, and
    # the last save composes them onto the genie: ctgedit gives (1, -0.0, +0.0, -1) mode
    # 0x27; B +0.0 would mean CtDraw composes the group's Local in at the switch from the
    # mirror to the turns, or at each operation)
    (
        "T33_genie_in_group_mirror_two_turns",
        "FxMembers",
        ["FxGenieInGroup"],
        ["H", "R", "R"],
        _t("test_turn_memory"),
    ),
    # (a text in a plain group stores its old box turned as its Bounds, T11; does the save
    # keep that box in memory? ctgedit drops it, as for a top-level text: the second turn
    # turns the measured extent, the text 'after' gets rect2 (420, 59, 449, 76); keeping
    # the box would give (420, 60, 449, 76))
    ("T34_big_group_turn_save_turn", "FxArrange", ["FxBigGroup"], ["R", "S", "R"], _t("test_turn_memory")),
    # (the save between a mirror and turns in a plain group: the save composes the group's
    # mirror and first turn (mode 0x17) onto the genie, and the next turn's group Local ORs
    # 0x15 into it: ctgedit gives (1, -0.0, +0.0, -1) mode 0x17; merging the quarter
    # nibbles as a turn of the genie's own Local does would give mode 0x27)
    (
        "T35_genie_in_group_mirror_turn_save_turn",
        "FxMembers",
        ["FxGenieInGroup"],
        ["H", "R", "S", "R"],
        _t("test_turn_memory"),
    ),
    # (the same for a mirrored text in a plain group, FxPair's FxPairB: ctgedit gives (1,
    # -0.0, +0.0, -1) mode 0x17; turning the text's own Local gives mode 0x27)
    ("T36_pair_mirror_turn_save_turn", "FxMembers", ["FxPair"], ["H", "R", "S", "R"], _t("test_turn_memory")),
    # (a text in a plain group turned first, then mirrored: CtDraw gives orient 1, echo
    # (20, 67) and (-1, 0, -0, 1, 80, 0) mode 0x17, the group's turn held in memory and the
    # mirror about x 40 (the text's box in memory); a turn put into the orient would give
    # orient 3 and (0, -1, -1, 0, 84.5, 84.5))
    ("T37_pair_turn_mirror_save_turn", "FxMembers", ["FxPair"], ["R", "H", "S", "R"], _t("test_turn_memory")),
    # (the half pixels of plain shapes in a plain group across a save: the first turn of
    # T11 and T34 is about the whole-pixel centre (235, 93), so no half pixel reaches the
    # save. With FxLoneText the centre is (387.5, 128); ctgedit drops the halves at the
    # save, as for a top-level shape, and gives the group rect1 (324, 130, 753, 196);
    # keeping them gives (324, 130, 754, 197))
    (
        "T38_big_group_text_turn_save_turn",
        "FxArrange",
        ["FxBigGroup", "FxLoneText"],
        ["R", "S", "R"],
        _t("test_turn_memory"),
    ),
    # (a top-level text's turn held in its own Local, as T37 shows for the group's: the
    # mirror composes onto it, and ctgedit gives orient 1, echo (720, 193) and (0, 1, 1, 0,
    # 548.5, -549.5) mode 0x17, the form of a test page record 21; turning
    # the orient gives orient 3 and (-1, 0, 0, 1, 1474, 0) mode 3)
    ("T39_text_turn_mirror", "FxArrange", ["FxLoneText"], ["R", "H"], _t("test_turn_memory")),
    # (whether a save makes a text's turn its orient for good, as it measures the box
    # again: ctgedit gives orient 3, echo (732, 170) and (-1, 0, 0, 1, 1473, 0) mode 3;
    # a turn kept in memory gives orient 1 and (0, 1, 1, 0, 547.5, -549.5) mode 0x17)
    ("T40_text_turn_save_mirror", "FxArrange", ["FxLoneText"], ["R", "S", "H"], _t("test_turn_memory")),
    # (a move of a plain group with a text turned since its load: ctgedit keeps the text's
    # Bounds, its box in memory, and mirrors about the group's box in memory moved along,
    # x 125: the rectangle (100, 100, 130, 140), the text (0, 1, 1, 0, 80, 80) mode 0x17;
    # Bounds measured again give x 124.5: the rectangle at x 99, the text's offset 79)
    (
        "T41_pair_turn_move_mirror",
        "FxMembers",
        ["FxPair"],
        ["R", ("at", 100, 100), "H"],
        _t("test_turn_memory"),
    ),
)


def turn_units() -> List[Unit]:
    return [
        Unit(
            "turns",
            tid,
            "CtDraw's %s of %s on FxMain\\%s, then PageSave." % (_ops_text(ops), ", ".join(names), page),
            sorted(set(feeds)),
            _run_turn,
            sources=["FxMain/%s.ctg" % page],
            data={"project": "FxMain", "page": page, "names": names, "ops": list(ops)},
        )
        for tid, page, names, ops, feeds in TURNS
    ]


def _ops_text(ops) -> str:
    words = {
        "R": "rotate",
        "H": "mirror horizontal",
        "V": "mirror vertical",
        "S": "save",
        "O": "save and reopen",
    }
    out = []
    for op in ops:
        if isinstance(op, str):
            out.append(words[op])
        elif op[0] == "at":
            out.append("PositionAt(%d, %d)" % (op[1], op[2]))
        else:
            out.append("select %s" % ", ".join(op[1]))
    return ", ".join(out)


#: the pies of the angle fold page: (start, end) as stored, unfolded
PIE_ANGLES = (0, 360, -360, 270, 300, -270, -200, -180, 180, -90, 200, 350, 45, -45, -181, 181, 359, -359, 90)


def pie_units() -> List[Unit]:
    return [
        Unit(
            "pies",
            "FxOraclePies",
            "CtDraw's PageOpen + PageSave of a page of %d pies whose angles are stored "
            "unfolded (-360 to 360): the angles CtDraw folds them to." % len(PIE_ANGLES),
            _t("test_arrange_correctness", "test_appearance_kinds"),
            _run_input_page,
            data={"project": "FxMain", "page": "FxOraclePies", "actions": []},
        )
    ]


def draw_units() -> List[Unit]:
    touch = ("touch", 1, "Beep()", "", 0)
    key = ("key", 0, "ENTER", "WinFree()", "", "", "")
    return [
        Unit(
            "draw",
            "rectangle",
            "A rectangle CtDraw draws itself (DrawRectangle) on a blank page, with no properties, saved.",
            _t("test_factory_defaults"),
            _run_input_page,
            data={"project": "FxMain", "page": "FxOracleDraw", "actions": [("rect", 100, 100, 200, 160)]},
        ),
        Unit(
            "draw",
            "touch_and_keys",
            "A rectangle CtDraw draws, with a touch command "
            "(PropertiesInputTouchPut) and a keyboard command (PropertiesInputKeyboardPutEx).",
            _t("test_input_records"),
            _run_input_page,
            data={
                "project": "FxMain",
                "page": "FxOracleDraw",
                "actions": [("rect", 300, 100, 400, 160), touch, key],
            },
        ),
        Unit(
            "draw",
            "touch_on_linked_symbol",
            "A touch command CtDraw puts on the linked symbol FxLinkedBox of FxSymbols.",
            _t("test_input_records"),
            _run_named_actions,
            sources=["FxMain/FxSymbols.ctg"],
            data={"project": "FxMain", "page": "FxSymbols", "names": ["FxLinkedBox"], "actions": [touch]},
        ),
        Unit(
            "draw",
            "touch_on_symbol_set",
            "A touch command CtDraw puts on the symbol set FxOnOffSet of FxSymbols.",
            _t("test_input_records", "test_symbol_sets"),
            _run_named_actions,
            sources=["FxMain/FxSymbols.ctg"],
            data={"project": "FxMain", "page": "FxSymbols", "names": ["FxOnOffSet"], "actions": [touch]},
        ),
        Unit(
            "draw",
            "copy_paste_named",
            "ClipboardCopy + ClipboardPaste of the named group "
            "FxManyGroup1 and of the named genie FxManyPump1 on FxMany: the names CtDraw "
            "gives the pasted copies.",
            _t("test_access_metadata_ai"),
            _run_copy_paste,
            sources=["FxMain/FxMany.ctg"],
            data={"project": "FxMain", "page": "FxMany", "copies": [["FxManyGroup1"], ["FxManyPump1"]]},
        ),
    ]


def place_units() -> List[Unit]:
    return [
        Unit(
            "place",
            "genie_into_handle_gaps",
            "LibraryObjectPlaceEx of fxgenie.fx_pump "
            "(Tag FX_PUMP1, Label Fx) onto a page whose records have the handles 0, 1, 2, 4, "
            "6, 13, then two rectangles: the handles CtDraw gives.",
            _t("test_open_load_rules"),
            _run_input_page,
            data={
                "project": "FxMain",
                "page": "FxOracleGaps",
                "actions": [
                    (
                        "place",
                        "FxLib",
                        "fxgenie",
                        "fx_pump",
                        "ctm",
                        300,
                        300,
                        [("Tag", "FX_PUMP1"), ("Label", "Fx")],
                    ),
                    ("rect", 600, 100, 650, 140),
                    ("rect", 600, 200, 650, 240),
                ],
            },
        ),
        Unit(
            "place",
            "attaching_genie",
            "LibraryObjectPlaceEx of fxgenie.fx_popper (it "
            "attaches !fx_popup) onto a blank page: the super genie page CtDraw writes, its "
            ".ctF and rows. FxMain's own !fx_popup (the fixture's FxPopper made it) is "
            "deleted first, files and rows, as the Open dialog's Delete does: CtDraw only "
            "creates a super genie page the project lacks.",
            _t("test_super_genies"),
            _run_input_page,
            data={
                "project": "FxMain",
                "page": "FxOraclePlace",
                "drop_pages": ["!fx_popup"],
                "actions": [
                    ("place", "FxLib", "fxgenie", "fx_popper", "ctm", 200, 200, [("Tag", "FX_PUMP1")])
                ],
            },
        ),
        Unit(
            "place",
            "nested_genie",
            "LibraryObjectPlaceEx of fxnest.n4 (Name Pump, Extra "
            "X) onto a blank page, in a fresh Graphics Builder, named FxNested as "
            "FxGenies names its copy: CtDraw's own placement of the nested chain.",
            _t("test_genie_nesting", "test_nested_recompose"),
            _run_input_page,
            data={
                "project": "FxMain",
                "page": "FxOraclePlace",
                "fresh": True,
                "actions": [
                    ("place", "FxLib", "fxnest", "n4", "ctm", 200, 200, [("Name", "Pump"), ("Extra", "X")]),
                    ("name", "FxNested"),
                ],
            },
        ),
        Unit(
            "place",
            "linked_symbol",
            "LibraryObjectPlaceEx of fxsym.fx_box, linked, onto a blank page.",
            _t("test_elements", "test_symbol_workflows"),
            _run_input_page,
            data={
                "project": "FxMain",
                "page": "FxOraclePlace",
                "actions": [("place", "FxLib", "fxsym", "fx_box", "ctl", 150, 150, [])],
            },
        ),
        Unit(
            "place",
            "grown_element",
            "LibraryObjectPlaceEx of fxmany.fx_last_big (the big "
            "last block of the grown fxmany.ctl) onto a blank page: the page and its .ctF.",
            _t("test_library_growth"),
            _run_input_page,
            data={
                "project": "FxMain",
                "page": "FxOraclePlace",
                "actions": [("place", "FxLib", "fxmany", "fx_last_big", "ctl", 200, 200, [])],
            },
        ),
    ]


# CtDraw's menu command ids (a posted WM_COMMAND runs one): Edit > Cut Link,
#: Edit > Break Lock Mode (a toggle: template objects
#: are selectable only while it is on), Edit > Select All
MENU_CUT_LINK = 138
MENU_BREAK_LOCK = 148
MENU_SELECT_ALL = 133
#: the page linked to fx_style normal@XGA whose template link the cut_link unit cuts
CUT_LINK_PAGE = "FxTplLinked"


def newpage_units() -> List[Unit]:
    out = [
        Unit(
            "newpage",
            "linked" if linked else "unlinked",
            "CtDraw's New Page (PageNew) from fx_style normal@XGA, %s, saved as "
            "FxOracleNew%s (PageSaveAs): the page and the rows CtDraw writes."
            % ("linked" if linked else "not linked", "Linked" if linked else "Unlinked"),
            _t("test_template_place", "test_templates", "test_page_lifecycle"),
            _run_new_page,
            sources=["FxStyle/fx_style.ctt"],
            data={"linked": linked},
        )
        for linked in (True, False)
    ]
    out.append(
        Unit(
            "newpage",
            "cut_link",
            "CtDraw's Edit > Cut Link (menu command %d) on "
            "FxMain\\%s with its template selected (Break Lock Mode on): the page "
            "after its template link is cut, saved, then that page after CtDraw's "
            "PageOpen + PageSave (reopened.ctg)." % (MENU_CUT_LINK, CUT_LINK_PAGE),
            _t("test_template_place", "test_templates"),
            _run_cut_link,
            sources=["FxMain/%s.ctg" % CUT_LINK_PAGE],
            data={"project": "FxMain", "page": CUT_LINK_PAGE},
        )
    )
    return out


#: the chain step: the nested genie chain, its library and the
#: element whose text the units edit (prepare maps its records' ANs)
CHAIN_LIB = ("FxLib", "fxnest.ctm")
CHAIN_EDITED = "n0"
#: the text the gained_member unit draws in n0 (DrawText(text, x, y), below n0's own)
CHAIN_GAINED = ("%Label% X", 10, 45)
#: the elements whose load + save follows an edit of n0, in order
CHAIN_FOLLOW = ("n1", "n2", "n3", "n4")
#: the template the outer_template unit makes (PageNewTemplate from the base, linked, then
#: PageSaveAsEx under this name), the base, and the rectangle drawn on the base after it
OUTER_PROJECT = "FxStyle"
OUTER_STYLE = "fx_style"
OUTER_BASE = ("normal", 3, True)  # template, PageNew resolution, title bar
OUTER_NAME = "fxouter"
INNER_RECT = (300, 300, 400, 360)
#: the shipped template that holds another one's copy (Include: bottom.alarm@-XGA quotes
#: bottom.NORMAL@-XGA), copied from the install's Include into the capture's FxStyle; its
#: output holds AVEVA content, so the unit writes only outside tests/fixtures/oracle
SHIPPED_LIB = "bottom.ctt"
SHIPPED_OUTER = ("alarm", 3, False)  # template, PageNew resolution, title bar
SHIPPED_INNER = "NORMAL@-XGA"
SHIPPED_PROJECT = "FxStyle"


def chain_units() -> List[Unit]:
    proj, lib = CHAIN_LIB
    src = ["%s/%s" % (SOURCE_OF[proj], lib)]
    nest = _t("test_oracle_chain", "test_genie_nesting", "test_nested_recompose")
    tpl = _t("test_oracle_chain", "test_templates", "test_template_place")
    return [
        Unit(
            "chain",
            "gained_member",
            "DrawText of %r in %s of %s\\%s (a member gained among "
            "kept ones), %s saved; then the load + save of %s in order, in the same Graphics "
            "Builder, and again from %s's save in a fresh one."
            % (CHAIN_GAINED[0], CHAIN_EDITED, proj, lib, CHAIN_EDITED, ", ".join(CHAIN_FOLLOW), CHAIN_EDITED),
            nest,
            _run_chain_gained,
            sources=src,
            data={"project": proj, "library": lib, "fresh": True},
        ),
        Unit(
            "chain",
            "mirror_back",
            "PositionMirrorHorizontal of %s's text, %s saved, the "
            "load + save of n1; then the text mirrored back, %s saved, the load + save of n1 "
            "(in the same Graphics Builder, and again in a fresh one)."
            % (CHAIN_EDITED, CHAIN_EDITED, CHAIN_EDITED),
            nest,
            _run_chain_mirror,
            sources=src,
            data={"project": proj, "library": lib, "fresh": True},
        ),
        Unit(
            "chain",
            "outer_template",
            "PageNewTemplate from %s %s@XGA, linked, saved as the "
            "new template %s (PageSaveAsEx): a template that holds another template's copy; "
            "its load + save unedited in a fresh Graphics Builder; a rectangle drawn on the "
            "inner %s@XGA and saved; the outer's load + save after it and a New Page from it "
            "(in the same Graphics Builder, and again in a fresh one)."
            % (OUTER_STYLE, OUTER_BASE[0], OUTER_NAME, OUTER_BASE[0]),
            tpl,
            _run_outer_template,
            sources=["FxStyle/%s.ctt" % OUTER_STYLE],
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
        Unit(
            "chain",
            "shipped_template",
            "The install's Include %s copied into the capture's "
            "%s: CtDraw's load + save of %s@-XGA unedited (it holds %s's copy) in a fresh "
            "Graphics Builder; a rectangle drawn on %s and saved; the outer's load + save "
            "after it and a New Page from it. AVEVA content: it runs only with --out outside "
            "tests\\fixtures\\oracle and is never committed."
            % (SHIPPED_LIB, SHIPPED_PROJECT, SHIPPED_OUTER[0], SHIPPED_INNER, SHIPPED_INNER),
            _t("test_oracle_chain"),
            _run_shipped_template,
            data={"project": SHIPPED_PROJECT, "fresh": True, "scratch_only": True},
        ),
    ]


#: the chain4 step. Q1: the text that gives the outer template
#: a parameter of its own (DrawText(text, x, y), in a free part of fxouter@XGA)
OUTER_PARAM = ("%P%", 500, 500)
#: Q1 on shipped content: Include TOP.CTT doubletrend@XGA holds top.NORMAL@XGA's copy in
#: the element form and quotes an older version of it; ICONS.CTL goes with it (the
#: fixture's FxInclude icons.ctl would shadow the symbols its import table names), into
#: FxStyle and FxMain (a page's library search reaches FxInclude through FxLib first)
SHIPPED4_LIBS = (("TOP.CTT", ("FxStyle",)), ("ICONS.CTL", ("FxStyle", "FxMain")))
SHIPPED4_OUTER = ("doubletrend", 3, True)  # template, PageNew resolution, title bar
SHIPPED4_INNER = "NORMAL@XGA"
#: Q2: the texts the mirror_resize unit draws on its blank page ((caption, x, y, mirrored,
#: composed)), the rectangle, and the scale each gets about its box's top left (the drag of
#: a box's bottom right handle): AttributeTransformationMatrixPut of the scale composed onto
#: the object's matrix, or (not composed) of the scale alone, which SETS the matrix
RESIZE_TEXTS = (
    ("Mirror me", 100, 100, True, True),
    ("Plain me", 100, 200, False, True),
    ("Set me", 300, 100, True, False),
)
RESIZE_RECT = (100, 300, 200, 360)
RESIZE_SCALE = 2.0
#: Q3: the genie the placeholder unit places (as place:genie_into_handle_gaps), turned once
#: (PositionRotate) and scaled about its box's top left
PLACEHOLDER_GENIE = ("FxLib", "fxgenie", "fx_pump", "ctm", 300, 300, [("Tag", "FX_PUMP1"), ("Label", "Fx")])
PLACEHOLDER_SCALE = 1.5
#: Q4: the genie without parameters pasted into n0 (LibraryObjectPlaceEx in its window),
#: where, and the text it gains after
PARAMLESS = ("FxLib", "fxgenie", "fx_plain")
PARAMLESS_AT = (100, 10)
PARAMLESS_GAINED = ("%P%", 0, 25)
#: Q5: a placement whose value starts with a blank, and the page linked to the outer
#: template whose Cut Link the cut_link_outer unit makes
LABEL_SPACE = ("FxLib", "fxnest", "n0", "ctm", 200, 200, [("Label", " L1")])
OUTER_LINKED_PAGE = "FxOracleOuterLinked"


def chain4_units() -> List[Unit]:
    nest = _t("test_oracle_chain4", "test_genie_nesting", "test_nested_recompose")
    tpl = _t("test_oracle_chain4", "test_templates", "test_template_place")
    fx_style = ["FxStyle/%s.ctt" % OUTER_STYLE]
    return [
        Unit(
            "chain4",
            "outer_param",
            "PageNewTemplate from %s %s@XGA, linked, saved as %s "
            "(PageSaveAsEx); DrawText of %r on it, saved (its first own parameter: the copy's "
            "form in a template with a parameter level); its load + save in a fresh Graphics "
            "Builder; a rectangle drawn on %s@XGA and saved; the outer's load + save (an "
            "element-form copy composed) and a New Page from it, in the same Graphics Builder "
            "and again in a fresh one (the New Page first)."
            % (OUTER_STYLE, OUTER_BASE[0], OUTER_NAME, OUTER_PARAM[0], OUTER_BASE[0]),
            tpl,
            _run_outer_param,
            sources=fx_style,
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
        Unit(
            "chain4",
            "shipped_doubletrend",
            "The install's Include %s (with %s) copied into "
            "the capture's projects: CtDraw's load + save of %s@XGA unedited (its element-form "
            "copy of %s quotes an older version) in a fresh Graphics Builder and a New Page "
            "from it; again from the copy in a fresh one, the New Page first. AVEVA content: "
            "it runs only with --out outside tests\\fixtures\\oracle and is never committed."
            % (SHIPPED4_LIBS[0][0], SHIPPED4_LIBS[1][0], SHIPPED4_OUTER[0], SHIPPED4_INNER),
            _t("test_oracle_chain4"),
            _run_shipped_doubletrend,
            data={"project": "FxStyle", "fresh": True, "scratch_only": True},
        ),
        Unit(
            "chain4",
            "mirror_resize",
            "On a blank page: DrawText of %s and a rectangle, "
            "saved; the first and the last text mirrored (PositionMirrorHorizontal), saved; "
            "each object scaled %g times about its box's top left "
            "(AttributeTransformationMatrixPut of the scale composed onto the object's "
            "matrix; for the last text the scale alone), saved; the page's load + save in a "
            "fresh Graphics Builder. Then in fxnest n0: its text mirrored, saved, scaled the "
            "composed way, saved, and n1's load + save."
            % (", ".join(repr(t[0]) for t in RESIZE_TEXTS), RESIZE_SCALE),
            _t("test_oracle_chain4", "test_save_chain_geometry"),
            _run_mirror_resize,
            sources=["%s/%s" % (SOURCE_OF[CHAIN_LIB[0]], CHAIN_LIB[1])],
            data={"project": "FxMain", "page": "FxOracleDraw", "fresh": True},
        ),
        Unit(
            "chain4",
            "placeholder_turned",
            "LibraryObjectPlaceEx of %s.%s onto a blank page, "
            "turned (PositionRotate) and scaled %g times (AttributeTransformationMatrixPut), "
            "saved; FxLib\\%s.%s removed and the page's load + save in a fresh Graphics "
            "Builder (a placeholder); the library put back and the page's load + save in a "
            "fresh one."
            % (
                PLACEHOLDER_GENIE[1],
                PLACEHOLDER_GENIE[2],
                PLACEHOLDER_SCALE,
                PLACEHOLDER_GENIE[1],
                PLACEHOLDER_GENIE[3],
            ),
            _t("test_oracle_chain4", "test_save_chain_reports3"),
            _run_placeholder_turned,
            sources=["FxLib/%s.%s" % (PLACEHOLDER_GENIE[1], PLACEHOLDER_GENIE[3])],
            data={"project": "FxMain", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain4",
            "param_gained",
            "LibraryObjectPlaceEx of %s.%s (a genie without "
            "parameters) into fxnest n0, saved, n1's load + save; DrawText of %r in %s, saved "
            "(it gains a parameter); then the load + save of n0 and n1 in the same Graphics "
            "Builder and again in a fresh one."
            % (PARAMLESS[1], PARAMLESS[2], PARAMLESS_GAINED[0], PARAMLESS[2]),
            nest,
            _run_param_gained,
            sources=["%s/%s" % (SOURCE_OF[CHAIN_LIB[0]], CHAIN_LIB[1]), "FxLib/%s.ctm" % PARAMLESS[1]],
            data={"project": "FxLib", "library": CHAIN_LIB[1], "fresh": True},
        ),
        Unit(
            "chain4",
            "label_space",
            "LibraryObjectPlaceEx of %s.%s with %s = %r (a value "
            "that starts with a blank) onto a blank page, saved; saved again; the page's load "
            "+ save in a fresh Graphics Builder."
            % (LABEL_SPACE[1], LABEL_SPACE[2], LABEL_SPACE[6][0][0], LABEL_SPACE[6][0][1]),
            nest,
            _run_label_space,
            sources=["FxLib/%s.%s" % (LABEL_SPACE[1], LABEL_SPACE[3])],
            data={"project": "FxMain", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain4",
            "cut_link_outer",
            "PageNewTemplate from %s %s@XGA, saved as %s; a New "
            "Page from it saved as %s; a rectangle drawn on %s@XGA and saved; Cut Link of the "
            "page's template (Break Lock Mode on, menu %d), in the same Graphics Builder; "
            "from the page as made, its load + save in a fresh one, and its Cut Link in "
            "another fresh one."
            % (OUTER_STYLE, OUTER_BASE[0], OUTER_NAME, OUTER_LINKED_PAGE, OUTER_BASE[0], MENU_CUT_LINK),
            tpl,
            _run_cut_link_outer,
            sources=fx_style,
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
    ]


#: the chain5 step. Q7: the texts n0's text
#: takes in turn (the AttributeText put of the selected text), and the placement of n1 on a
#: blank page after each
PARAM_DEFAULTS = ("%Label% %Q=5%", "%Label% %Q=7%")
PARAM_DEFAULT_PLACE = ("FxLib", "fxnest", "n1", "ctm", 200, 200, [("Name", "P1")])
#: Q8: the elements placed into n0 (LibraryObjectPlaceEx in its window, object type 0) and
#: where: n0 itself, then n2 (which nests n1, which nests n0)
CYCLE_PLACES = (("n0", 200, 10), ("n2", 200, 60))
#: Q9 (a): the genie placed on a blank page and the text its text becomes after
LABEL_GENIE = ("FxLib", "fxgenie", "fx_value", "ctm", 100, 100, [("Description", "Fx Start")])
LABEL_EDITED = "@(%Description% EDITED)"
#: Q9 (b): the texts drawn in fx_plain, each given a display value (PropertiesDisplayValuePut
#: of its type and expressions, then PropertiesDisplayValueTextPut of each row): (text, x, y,
#: type 0 On / Off, 1 Multi-state, 2 Array, the five expressions, the rows)
STATE_GENIE = ("FxLib", "fxgenie", "fx_plain")
STATE_TEXTS = (
    (
        "@(Running)",
        0,
        40,
        1,
        ("FX_A", "FX_B", "", "", ""),
        ("@(Running)", "@(Stopped)", "@(Fault)", "@(Manual)"),
    ),
    ("@(Auto)", 0, 60, 2, ("FX_IDX", "", "", "", ""), ("@(Auto)", "@(Hand)", "@(Off Mode)")),
    ("@(Fx Off)", 0, 80, 0, ("FX_BIT", "", "", "", ""), ("@(Fx Off)", "@(Fx On)")),
)
STATE_PLACE = ("FxLib", "fxgenie", "fx_plain", "ctm", 100, 100, [])
#: Q10: the genie whose save writes a placeholder (it nests fxnest.n1) and the library made
#: missing for that save
PLACEHOLDER_HOST = ("FxLib", "fxparts", "fx_row", "ctm", 100, 100, [("RowNum", "7")])
PLACEHOLDER_MISSING = ("FxLib", "fxnest.ctm")
#: Q11: the page made from normal@XGA that also places fx_value, and the rectangles fx_value
#: gains
ORDER_PAGE = "FxOracleOrder"
ORDER_GENIE = ("FxLib", "fxgenie", "fx_value", "ctm", 300, 300, [("Description", "Fx Start")])
ORDER_RECTS = ((0, 40, 50, 60), (70, 40, 120, 60))
#: Q12: the shipped genies placed on a blank page (their own tables quote older nested
#: stamps) and the shipped files checked unchanged after
SHIPPED5_PLACES = (
    ("Include", "xp_controls", "Button", "ctm", 100, 100, []),
    ("Include", "cicode", "cicodefunction6args", "ctm", 300, 300, []),
)
SHIPPED5_FILES = ("xp_controls.ctm", "xp_buttons.ctl", "cicode.ctm")
#: Q13: the member of the outer template's copy of normal@XGA that gets a touch command
#: (its box in the copy) and the command (PropertiesInputTouchPut)
BOUND_MEMBER = (964, 40, 1004, 60)
BOUND_TOUCH = (1, "Beep()", "", 0)
#: Q14: the box the element-form copy's free DummyMarker is given before a load + save
#: (anchor, rect1, rect2: as normal@XGA stores its own)
MARKER_BOX = ((700, 118), (700, 100, 803, 119), (700, 100, 803, 119))
#: Q15: File > Save (posted to the frame, so the Save Template warning can show) and the
#: pages linked to the outer template and to normal@XGA itself
MENU_SAVE = 105
WARNING_PAGES = (OUTER_LINKED_PAGE, "FxTplLinked")
#: Q16: two groups on a blank page ((text, x, y, mirrored, the rectangle grouped with it)),
#: each scaled RESIZE_SCALE times; two texts whose font size is put (plain and mirrored)
GROUP_TEXTS = (
    ("Mirror grp", 100, 100, True, (100, 140, 200, 180)),
    ("Plain grp", 400, 100, False, (400, 140, 500, 180)),
)
FONT_TEXTS = (("Font plain", 100, 400, False), ("Font mirror", 400, 400, True))
FONT_SIZE = 22


def chain5_units() -> List[Unit]:
    nest = _t("test_oracle_chain5", "test_genie_nesting", "test_nested_recompose")
    tpl = _t("test_oracle_chain5", "test_templates", "test_template_place")
    labels = _t("test_oracle_chain5", "test_lang_labels")
    holders = _t("test_oracle_chain5", "test_placeholder_genies", "test_save_chain_stamps")
    fxnest = ["FxLib/fxnest.ctm"]
    fxgenie = ["FxLib/fxgenie.ctm"]
    fx_style = ["FxStyle/%s.ctt" % OUTER_STYLE]
    return [
        Unit(
            "chain5",
            "stale_host",
            "DrawText of %r in fxnest n0, n0 saved; the load + save "
            "of n2 while n1 is not saved since (its copy of n0 is stale), then of n1 and n2; "
            "in the same Graphics Builder and again from n0's save in a fresh one." % CHAIN_GAINED[0],
            nest,
            _run_stale_host,
            sources=fxnest,
            data={"project": "FxLib", "library": "fxnest.ctm", "fresh": True},
        ),
        Unit(
            "chain5",
            "param_default",
            "fxnest n0's text set to %r (the AttributeText put), "
            "n0 saved, n1's load + save and a placement of n1 on a blank page; then to %r, "
            "the same; then n1's load + save and a placement from n0's second save in a "
            "fresh Graphics Builder." % PARAM_DEFAULTS,
            nest,
            _run_param_default,
            sources=fxnest,
            data={"project": "FxLib", "library": "fxnest.ctm", "fresh": True},
        ),
        Unit(
            "chain5",
            "label_edit",
            "LibraryObjectPlaceEx of fxgenie.fx_value with Description "
            "'Fx Start' on a blank page, saved; fx_value's text set to %r and saved; the "
            "page's load + save in the same Graphics Builder and in a fresh one: its table's "
            "labels." % LABEL_EDITED,
            labels,
            _run_label_edit,
            sources=fxgenie,
            data={"project": "FxMain", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain5",
            "label_states",
            "In fxgenie fx_plain: a multi-state text, an array text "
            "and an On / Off text drawn (PropertiesDisplayValuePut and "
            "PropertiesDisplayValueTextPut, every row an '@()' label), saved; its load + save "
            "in a fresh Graphics Builder; a placement of fx_plain on a blank page: the "
            "labels of the element's and the page's tables and their order.",
            labels,
            _run_label_states,
            sources=fxgenie,
            data={"project": "FxLib", "library": "fxgenie.ctm", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain5",
            "element_placeholder",
            "FxLib\\fxnest.ctm removed and fxparts fx_row "
            "(it nests fxnest.n1) opened and saved: the element holds a placeholder. A "
            "placement of fx_row on a blank page, the page's load + save with fxnest "
            "missing and with it back, a new placement with it back; fx_row's load + save "
            "with it back, as saved and with the bare zero-GUID entry removed from its "
            "import table (each in a fresh Graphics Builder).",
            holders,
            _run_element_placeholder,
            sources=["FxLib/fxparts.ctm", "FxLib/fxnest.ctm"],
            data={"project": "FxLib", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain5",
            "order",
            "New Page from %s %s@XGA, linked, saved as %s; fxgenie.fx_value "
            "placed on it, saved; fx_value gains two rectangles, saved; normal@XGA opened "
            "and saved (it nests fx_value); the page's load + save in the same Graphics "
            "Builder and in a fresh one: the new records' handles and ANs."
            % (OUTER_STYLE, OUTER_BASE[0], ORDER_PAGE),
            tpl + _t("test_update_pages"),
            _run_order,
            sources=fx_style + fxgenie,
            data={"project": "FxMain", "page": ORDER_PAGE, "fresh": True},
        ),
        Unit(
            "chain5",
            "extra_binding",
            "PageNewTemplate from %s %s@XGA saved as %s; in it the "
            "copy's rectangle at %r given a touch command (Break Lock Mode on, "
            "PropertiesInputTouchPut), saved; a rectangle drawn on normal@XGA and saved; the "
            "outer's load + save in the same Graphics Builder and in a fresh one: the "
            "binding the inner template's record lacks, kept or dropped."
            % (OUTER_STYLE, OUTER_BASE[0], OUTER_NAME, BOUND_MEMBER),
            tpl,
            _run_extra_binding,
            sources=fx_style,
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
        Unit(
            "chain5",
            "marker_box",
            "PageNewTemplate from %s %s@XGA saved as %s, DrawText of "
            "%r on it, saved (the copy in the element form); the copy's free DummyMarker "
            "given the box %r in the library; the outer's load + save in a fresh Graphics "
            "Builder: the box derived again or kept."
            % (OUTER_STYLE, OUTER_BASE[0], OUTER_NAME, OUTER_PARAM[0], MARKER_BOX[1]),
            tpl,
            _run_marker_box,
            sources=fx_style,
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
        Unit(
            "chain5",
            "template_warning",
            "PageNewTemplate from %s %s@XGA saved as %s and a "
            "New Page from it saved as %s; a rectangle drawn on normal@XGA and saved with "
            "File > Save (menu %d, message boxes answered Yes): the Save Template warning and "
            "what it does; when it updates no page, Tools > Update Pages over COM "
            "(ProjectUpdatePages) on FxMain: the pages linked to the outer and to normal@XGA, "
            "and whether the outer is saved."
            % (OUTER_STYLE, OUTER_BASE[0], OUTER_NAME, OUTER_LINKED_PAGE, MENU_SAVE),
            tpl,
            _run_template_warning,
            sources=fx_style,
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
        Unit(
            "chain5",
            "scaled_groups",
            "On a blank page: a group of a mirrored text and a "
            "rectangle, a group of a plain text and a rectangle (PageGroupSelectedObjects), "
            "saved; each scaled %g times about its box's top left "
            "(AttributeTransformationMatrixPut composed onto its matrix); a plain and a "
            "mirrored text given the font size %d (the AttributeTextFontSize put), saved; "
            "the page's load + save in a fresh Graphics Builder." % (RESIZE_SCALE, FONT_SIZE),
            _t("test_oracle_chain5", "test_save_chain_geometry"),
            _run_scaled_groups,
            data={"project": "FxMain", "page": "FxOracleDraw", "fresh": True},
        ),
        Unit(
            "chain5",
            "shipped_nested",
            "LibraryObjectPlaceEx of the install's Include "
            "xp_controls.Button and cicode.cicodefunction6args (their own tables quote older "
            "nested stamps) on a blank page, saved; its load + save in a fresh Graphics "
            "Builder; the shipped libraries checked unchanged. AVEVA content: it runs only "
            "with --out outside tests\\fixtures\\oracle and is never committed.",
            _t("test_oracle_chain5", "test_update_pages"),
            _run_shipped_nested,
            data={"project": "FxMain", "page": "FxOraclePlace", "fresh": True, "scratch_only": True},
        ),
        Unit(
            "chain5",
            "cycles",
            "In fxnest n0's window: LibraryObjectPlaceEx of n0 itself, "
            "then (from the library as it was, in a fresh Graphics Builder) of n2, which nests "
            "n1, which nests n0; each followed by a save: what CtDraw answers (a message, an "
            "error, a hang the watchdog ends) and what it writes. Run it last, on its own, "
            "with a short --com-deadline.",
            nest,
            _run_cycles,
            sources=fxnest,
            data={"project": "FxLib", "library": "fxnest.ctm", "fresh": True},
        ),
    ]


#: the chain6 step. Q17: the texts the text_resize unit draws on
#: a blank page ((caption, x, y, mirrored first, (sx, sy))): each scaled by (sx, sy) about its
#: box's top left (AttributeTransformationMatrixPut composed onto its matrix), then the
#: mirrored ones mirrored back (a second PositionMirrorHorizontal)
TEXT_RESIZE = (
    ("Plain wide", 100, 100, False, (2.0, 1.0)),
    ("Plain tall", 400, 100, False, (1.0, 2.0)),
    ("Mirror back", 100, 300, True, (2.0, 2.0)),
    ("Mirror wide", 400, 300, True, (2.0, 1.0)),
)
#: Q18: the texts that give the outer template two parameters of its own (DrawText(text, x,
#: y)): one without a default, one with
OUTER_MIXED = (("%P%", 500, 500), ("%Q=5%", 500, 540))
#: Q19: the genie placed on a blank page with nothing bound and copied (ClipboardCopy), the
#: genie without parameters it is pasted into (ClipboardPaste), the host that nests that one
#: (LibraryObjectPlaceEx into the host's window at (x, y)), the text the pasted-into genie
#: gains after, and the placement of the host (its puts of P and Label may be refused)
PAGE_COPY = ("FxLib", "fxnest", "n0", "ctm", 100, 100, [])
PAGE_COPY_INTO = ("FxLib", "fxgenie.ctm", "fx_plain")
COPY_HOST = ("FxLib", "fxparts.ctm", "fx_none", 40, 0)
COPY_GAINED = ("%P%", 0, 25)
COPY_HOST_PLACE = ("FxLib", "fxparts", "fx_none", "ctm", 200, 200, [("P", "PV"), ("Label", "LV")])
#: Q20: the placement of n2 on a blank page, made before n0 gains CHAIN_GAINED and n1 is saved
#: (n2 is not saved after)
STALE_PLACE = ("FxLib", "fxnest", "n2", "ctm", 200, 200, [("Name", "P2")])
#: Q21: the template saved while the library of the genie it nests is missing (a placeholder
#: in the template), that library, and the fixture page linked to the template
HOLDER_TEMPLATE = ("FxStyle", "fx_style.ctt", "normal@XGA")
HOLDER_MISSING = ("FxLib", "fxgenie.ctm")
HOLDER_PAGE = "FxTplLinked"
#: when the template's own save writes no placeholder: the template made from it, unlinked,
#: with the library missing, and the page made linked to that one; the New Page after
HOLDER_NAME = "fxholder"
HOLDER_LINKED_PAGE = "FxOracleHolderPage"
HOLDER_NEW_PAGE = "FxOracleHolderNew"
#: Q22: the placement copied (ClipboardCopy) and the element whose window it is pasted into
#: (ClipboardPaste); the ANs whose texts are read in that window (AttributeText, only where
#: AttributeClass says Text)
PASTE_PLACE = ("FxLib", "fxnest", "n2", "ctm", 100, 100, [("Name", "P")])
PASTE_INTO = ("FxLib", "fxnest.ctm", "n3")
SHOWN_ANS = 60


def chain6_units() -> List[Unit]:
    nest = _t("test_oracle_chain6", "test_genie_nesting", "test_nested_recompose")
    tpl = _t("test_oracle_chain6", "test_templates", "test_template_place")
    fxnest = ["FxLib/fxnest.ctm"]
    fx_style = ["FxStyle/%s.ctt" % OUTER_STYLE]
    return [
        Unit(
            "chain6",
            "text_resize",
            "On a blank page: DrawText of %s, saved; the texts %s "
            "mirrored (PositionMirrorHorizontal), saved; each scaled about its box's top left "
            "by its own x and y factors (%s; AttributeTransformationMatrixPut composed onto "
            "its matrix), saved; the mirrored ones mirrored back, saved; the page's load + "
            "save in a fresh Graphics Builder."
            % (
                ", ".join(repr(t[0]) for t in TEXT_RESIZE),
                " and ".join(repr(t[0]) for t in TEXT_RESIZE if t[3]),
                ", ".join("%s %g x %g" % (t[0], t[4][0], t[4][1]) for t in TEXT_RESIZE),
            ),
            _t("test_oracle_chain6", "test_save_chain_geometry"),
            _run_text_resize,
            data={"project": "FxMain", "page": "FxOracleDraw", "fresh": True},
        ),
        Unit(
            "chain6",
            "outer_mixed",
            "PageNewTemplate from %s %s@XGA, linked, saved as %s; "
            "DrawText of %s on it, saved (two own parameters, one with a default); a New Page "
            "from it; the outer's load + save and a New Page after it; again from its save "
            "in a fresh Graphics Builder: a New Page, the outer's load + save, a New Page."
            % (OUTER_STYLE, OUTER_BASE[0], OUTER_NAME, " and ".join(repr(t[0]) for t in OUTER_MIXED)),
            tpl,
            _run_outer_mixed,
            sources=fx_style,
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
        Unit(
            "chain6",
            "paramless_copy",
            "%s.%s placed on a blank page with nothing bound, "
            "copied (ClipboardCopy) and pasted (ClipboardPaste) into %s (a genie without "
            "parameters), saved; %s placed into %s.%s, saved; DrawText of %r in %s, saved; "
            "%s's load + save and a placement of it (P and Label put), in the same Graphics "
            "Builder and again in a fresh one."
            % (
                PAGE_COPY[1],
                PAGE_COPY[2],
                PAGE_COPY_INTO[2],
                PAGE_COPY_INTO[2],
                os.path.splitext(COPY_HOST[1])[0],
                COPY_HOST[2],
                COPY_GAINED[0],
                PAGE_COPY_INTO[2],
                COPY_HOST[2],
            ),
            nest,
            _run_paramless_copy,
            sources=["FxLib/fxparts.ctm", "FxLib/fxgenie.ctm", "FxLib/fxnest.ctm"],
            data={"project": "FxLib", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain6",
            "stale_page",
            "%s.%s placed on a blank page with Name %r, saved; "
            "DrawText of %r in fxnest n0, saved; n1's load + save (n2 not saved); the page's "
            "load + save in the same Graphics Builder and, from the page as placed, in a "
            "fresh one, and a new placement of n2 there."
            % (STALE_PLACE[1], STALE_PLACE[2], STALE_PLACE[6][0][1], CHAIN_GAINED[0]),
            nest,
            _run_stale_page,
            sources=fxnest,
            data={"project": "FxLib", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain6",
            "template_placeholder",
            "%s\\%s removed and %s %s opened and saved, "
            "again in a Graphics Builder started without it; when neither save writes a "
            "placeholder, PageNewTemplate from it, unlinked, saved as %s, and a New Page "
            "linked to that one. The library put back; in a fresh Graphics Builder the load + "
            "save of the page linked to the template that holds the placeholder (%s, or the "
            "new page), a New Page from that template, its own load + save and the page's "
            "load + save after it (from the page as it was), and that load + save again in "
            "a fresh one."
            % (
                HOLDER_MISSING[0],
                HOLDER_MISSING[1],
                os.path.splitext(HOLDER_TEMPLATE[1])[0],
                HOLDER_TEMPLATE[2],
                HOLDER_NAME,
                HOLDER_PAGE,
            ),
            tpl + _t("test_placeholder_genies"),
            _run_template_placeholder,
            sources=[
                "FxStyle/%s" % HOLDER_TEMPLATE[1],
                "FxLib/%s" % HOLDER_MISSING[1],
                "FxMain/%s.ctg" % HOLDER_PAGE,
            ],
            data={"project": "FxMain", "page": HOLDER_PAGE, "fresh": True},
        ),
        Unit(
            "chain6",
            "pasted_copy",
            "%s.%s placed on a blank page with Name %r, copied "
            "(ClipboardCopy) and pasted (ClipboardPaste) into %s's window: the texts the "
            "window shows (AttributeText of each text AN), then %s saved; its load + save in "
            "a fresh Graphics Builder, the texts read again."
            % (PASTE_PLACE[1], PASTE_PLACE[2], PASTE_PLACE[6][0][1], PASTE_INTO[2], PASTE_INTO[2]),
            nest,
            _run_pasted_copy,
            sources=fxnest,
            data={"project": "FxLib", "page": "FxOraclePlace", "fresh": True},
        ),
    ]


#: the chain7 step. Q24 (a): a template saved over
#: itself: PageNewTemplate from fx_style normal@XGA (linked), PageSaveAsEx under normal's own
#: name (each spelling tried); then, from the library as it was, the same from the outer
#: fxouter (which holds normal's copy): a template that would hold itself one copy down
SELF_NAMES = ("normal", "normal@XGA")
#: Q24 (b): the template holding a placeholder (chain6 template_placeholder: normal@XGA saved
#: while fxgenie.ctm did not open; an oracle capture of fixture content) and the pages made
#: from it and cut from it with the library back
HOLDER_CAPTURE = ("chain6", "template_placeholder", "template_missing_fresh/library.ctt")
HOLDER7_LINKED = "FxOracleHolderLinked"
HOLDER7_UNLINKED = "FxOracleHolderUnlinked"
#: Q24 (c): the element holding a placeholder (chain5 element_placeholder: fx_row saved while
#: fxnest.ctm was missing; its placeholder names fxnest.n1), its content copied into fxnest
#: under the name it names (n1: a genie holding a placeholder of itself) and under n0 (n1
#: nests n0: a genie holding a placeholder of a genie that nests it)
SELF_HOLDER_CAPTURE = ("chain5", "element_placeholder", "element_missing/library.ctm")
SELF_HOLDER_FROM = ("fxparts.ctm", "fx_row")
SELF_HOLDER_AS = ("n1", "n0")
#: Q25 (a): the genie without parameters placed on a page, copied and pasted into n0's window
PASTE7_PLACE = ("FxLib", "fxgenie", "fx_plain", "ctm", 100, 100, [])
PASTE7_INTO = ("FxLib", "fxnest.ctm", "n0")
#: Q25 (b): the page made by New Page from fxouter@XGA once it has '%P%' (OUTER_PARAM)
PARAM_PAGE = "FxOracleParamPage"
#: Q25 (c): the genies placed into fxouter@XGA once it has '%P%' ((project, library,
#: element, x, y, the property puts)) and the page made from it after
OUTER_GENIES = (
    ("FxLib", "fxnest", "n0", 600, 600, [("Label", "TL")]),
    ("FxLib", "fxgenie", "fx_plain", 600, 650, []),
)
OUTER_GENIE_PAGE = "FxOracleOuterGenie"
#: Q25 (d): n0's text with a leading blank (the AttributeText put), a copy of n0 placed into
#: n1 with a binding that starts with a blank, and a placement of n1 after
BLANK_TEXT = " %Label%"
BLANK_COPY = ("FxLib", "fxnest", "n0", 10, 70, [("Label", " %Name% L1")])
BLANK_PLACE = ("FxLib", "fxnest", "n1", "ctm", 200, 200, [("Name", "P1")])
#: Q26: the pages open in CtDraw's editor while normal@XGA is saved in another window: the
#: fixture page linked to it and the page made from the outer template built on it
OPEN_PAGES = ("FxTplLast", OUTER_LINKED_PAGE)
#: CtDraw's page types of a template and of a genie for PageOpenEx
TEMPLATE_PAGE_TYPE = 3
GENIE_PAGE_TYPE = 1
#: Q28 (a): shipped elements whose own record's value bag differs from its template bag
#: ((the install's project, library, element)), the library one of them names, and the
#: capture project they are copied into
SHIPPED7_ELEMENTS = (
    ("Example", "energydemo.ctm", "Value_noAlarm"),
    ("ExampleSA", "sa_dataobjects.ctm", "ClockTimer"),
)
SHIPPED7_EXTRA = (("SA_Library", "sa_clocktimer_24.ctl"),)
SHIPPED7_PROJECT = "FxLib"
#: Q28 (b): the symbols of one DummyMarker the tool makes in fxsym.ctl ((name, the marker's
#: anchor, rect1 = rect2, its class part (hotspot rect + point) and its slots, None for
#: normal@XGA's own)): normal@XGA's free marker, and a point marker whose class part is zero
#: under a scale in its slots (the form a library holds)
MARKER_LIB = ("FxLib", "fxsym.ctl")
MARKER_FROM = ("FxStyle", "fx_style.ctt", "normal@XGA")
MARKER_SYMBOLS = (
    ("fx_marker", (700, 118), (700, 100, 803, 119), None, None),
    (
        "fx_marker_scaled",
        (631, 361),
        (631, 361, 631, 361),
        ((0, 0, 0, 0), (0, 0)),
        (
            (3, (1.2, 0.0, 0.0, 1.2, 631.25, 361.0)),
            (3, (1.2, 0.0, 0.0, 1.2, -4.75, -31.0)),
            (3, (1.2, 0.0, 0.0, 1.2, 631.25, 361.0)),
        ),
    ),
)
#: Q28 (c): the shipped page whose texts a plain save re-measures
ABOUT_PAGE = ("Example", "!About")
#: Q28 (d): the array text drawn in fx_plain (its caption, its rows, the display value's
#: expression) and its placement, which binds Array1 only ('%Array0%' unbound)
ARRAY_TEXT = ("%Array0%", 0, 100)
ARRAY_ROWS = ("%Array0%", "%Array1%", "%Array2%")
ARRAY_EXPR = "FX_IDX"
ARRAY_PLACE = ("FxLib", "fxgenie", "fx_plain", "ctm", 100, 100, [("Array1", "A1")])
#: Q28 (e): the legacy Number (0x1004) and String (0x1005) records the tool puts on a blank
#: page: copies of FxLegacy2's (its Number and its String) with these value changes (None
#: removes the key), each with the stored extent of the copy or an odd one ('odd': anchor +
#: (0, -40, 200, 40)); named FxNum.. / FxStr.. in order
LEGACY_PAGE = "FxOracleLegacy"
LEGACY_FROM = "FxLegacy2"
LEGACY_ODD = (0, -40, 200, 40)
LEGACY_NUMBERS = (
    ({}, False),
    ({}, True),
    ({"FONT": None}, False),
    ({"FONT": "DefaultFont"}, False),
    ({"FONT": "FxBig"}, False),
    ({"FONT": "FxSmall"}, False),
    ({"FONT": "FxNoSuchFont"}, False),
    ({"EXPR": "FX_LEVEL1:#"}, False),
    ({"EXPR": "FX_LEVEL1:##########.##"}, False),
    ({"EXPR": "Time()"}, False),
    ({"EXPR": "FX_LEVEL1 + FX_LEVEL1 * 100 + FX_LEVEL1 * 10000"}, False),
    ({"FONT": "FxBig", "EXPR": "FX_LEVEL1:##########.##"}, False),
    ({"FONT": None, "EXPR": "FX_LEVEL1:###"}, True),
)
LEGACY_STRINGS = (
    ({}, False),
    ({}, True),
    ({"FONT": None}, False),
    ({"FONT": None, "ON_FONT": "FxBig", "OFF_FONT": "FxBig"}, False),
    ({"FONT": None, "ON_FONT": "FxSmall", "OFF_FONT": "FxSmall"}, False),
    ({"FONT": None, "ON_FONT": "FxBig", "OFF_FONT": "FxSmall"}, False),
    ({"FONT": None, "ON_FONT": "FxSP", "OFF_FONT": "FxSP"}, False),
    ({"FONT": None, "ON_TEXT": '"Hi"', "OFF_TEXT": '"Hi"'}, False),
    ({"FONT": None, "ON_TEXT": '"x"', "OFF_TEXT": '"A much longer string"'}, False),
    ({"FONT": None, "ON_TEXT": "", "OFF_TEXT": ""}, False),
    ({"FONT": None, "ON_TEXT": '"On"', "OFF_TEXT": "FX_TEXT"}, False),
    ({"FONT": None, "ON_FONT": "FxNoSuchFont", "OFF_FONT": "FxNoSuchFont"}, False),
    ({"FONT": "FxBig", "ON_FONT": None, "OFF_FONT": None}, True),
)


def chain7_units() -> List[Unit]:
    nest = _t("test_oracle_chain7", "test_genie_nesting", "test_nested_recompose")
    tpl = _t("test_oracle_chain7", "test_templates", "test_template_place")
    holders = _t("test_oracle_chain7", "test_placeholder_genies")
    fx_style = ["FxStyle/%s.ctt" % OUTER_STYLE]
    return [
        Unit(
            "chain7",
            "holder_pages",
            "The template %s %s as %s/%s captured it (a "
            "placeholder of fxgenie.fx_value), the library back: in a fresh Graphics Builder "
            "each, a linked New Page from it, an unlinked New Page, Cut Link of %s (linked "
            "to it) and Cut Link of the linked New Page."
            % (OUTER_STYLE, HOLDER_TEMPLATE[2], HOLDER_CAPTURE[0], HOLDER_CAPTURE[1], HOLDER_PAGE),
            holders + tpl[1:],
            _run_holder_pages,
            sources=fx_style + ["FxLib/fxgenie.ctm", "FxMain/%s.ctg" % HOLDER_PAGE],
            data={"project": "FxMain", "fresh": True},
        ),
        Unit(
            "chain7",
            "paste_paramless",
            "%s.%s placed on a blank page, copied "
            "(ClipboardCopy) and pasted (ClipboardPaste) into %s's window, saved; n1's load + "
            "save; then n0's and n1's in a fresh Graphics Builder."
            % (PASTE7_PLACE[1], PASTE7_PLACE[2], PASTE7_INTO[2]),
            nest,
            _run_paste_paramless,
            sources=["FxLib/fxnest.ctm", "FxLib/fxgenie.ctm"],
            data={"project": "FxLib", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain7",
            "outer_param_page",
            "fxouter made from %s %s@XGA, DrawText of %r on it, "
            "saved; a New Page from it saved as %s; a rectangle drawn on %s@XGA and saved; "
            "the page's load + save in the same Graphics Builder; from the page as made, its "
            "load + save in a fresh one, the outer's load + save and the page's again; "
            "Tools > Update Pages over COM on FxMain in another fresh one."
            % (OUTER_STYLE, OUTER_BASE[0], OUTER_PARAM[0], PARAM_PAGE, OUTER_BASE[0]),
            tpl,
            _run_outer_param_page,
            sources=fx_style,
            data={"project": "FxMain", "page": PARAM_PAGE, "fresh": True},
        ),
        Unit(
            "chain7",
            "genie_in_outer",
            "fxouter made from %s %s@XGA, DrawText of %r on it, "
            "saved; %s placed into it (LibraryObjectPlaceEx in its window, the puts after), "
            "saved; its load + save in a fresh Graphics Builder and a New Page from it."
            % (
                OUTER_STYLE,
                OUTER_BASE[0],
                OUTER_PARAM[0],
                " and ".join(
                    "%s.%s%s" % (g[1], g[2], " with %s" % dict(g[5]) if g[5] else "") for g in OUTER_GENIES
                ),
            ),
            tpl + nest[1:],
            _run_genie_in_outer,
            sources=fx_style + ["FxLib/fxnest.ctm", "FxLib/fxgenie.ctm"],
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
        Unit(
            "chain7",
            "label_blank",
            "fxnest n0's text set to %r (the AttributeText put), n0 "
            "saved, n1's load + save; n0 placed into n1 with %s = %r, n1 saved; a placement of "
            "n1 with %s = %r; n1's load + save in a fresh Graphics Builder."
            % (
                BLANK_TEXT,
                BLANK_COPY[5][0][0],
                BLANK_COPY[5][0][1],
                BLANK_PLACE[6][0][0],
                BLANK_PLACE[6][0][1],
            ),
            nest,
            _run_label_blank,
            sources=["FxLib/fxnest.ctm"],
            data={"project": "FxLib", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain7",
            "open_pages",
            "fxouter made from %s %s@XGA and a New Page from it saved "
            "as %s. In a fresh Graphics Builder: %s and %s opened in their windows, %s@XGA "
            "opened in a third, a rectangle drawn and saved with File > Save (menu %d, the "
            "message boxes answered Yes); each page's window then: what it shows (its objects "
            "and the template's, a shot) and its save. Again in another fresh one with the "
            "COM PageSave."
            % (
                OUTER_STYLE,
                OUTER_BASE[0],
                OUTER_LINKED_PAGE,
                OPEN_PAGES[0],
                OPEN_PAGES[1],
                OUTER_BASE[0],
                MENU_SAVE,
            ),
            tpl + _t("test_update_pages"),
            _run_open_pages,
            sources=fx_style + ["FxMain/%s.ctg" % OPEN_PAGES[0]],
            data={"project": "FxMain", "fresh": True},
        ),
        Unit(
            "chain7",
            "marker_symbol",
            "Symbols of one DummyMarker made in %s\\%s by the tool "
            "(%s): CtDraw's open + save of each, and again in a fresh Graphics Builder."
            % (MARKER_LIB[0], MARKER_LIB[1], ", ".join(m[0] for m in MARKER_SYMBOLS)),
            _t("test_oracle_chain7", "test_elements", "test_symbol_workflows"),
            _run_marker_symbol,
            sources=["FxLib/fxsym.ctl", "FxStyle/fx_style.ctt"],
            data={"project": "FxLib", "fresh": True},
        ),
        Unit(
            "chain7",
            "array_token",
            "In fxgenie fx_plain: DrawText of %r, an array display "
            "value (%s) with the rows %s, saved; a placement of fx_plain with %s = %r "
            "('%%Array0%%' unbound) on a blank page in a fresh Graphics Builder; the page's "
            "load + save there and in another fresh one."
            % (ARRAY_TEXT[0], ARRAY_EXPR, ", ".join(ARRAY_ROWS), ARRAY_PLACE[6][0][0], ARRAY_PLACE[6][0][1]),
            _t("test_oracle_chain7", "test_display_value", "test_save_chain_geometry"),
            _run_array_token,
            sources=["FxLib/fxgenie.ctm"],
            data={"project": "FxLib", "page": "FxOraclePlace", "fresh": True},
        ),
        Unit(
            "chain7",
            "legacy_measure",
            "A page the tool makes from %s's Number and String "
            "(%d Numbers, %d Strings: fonts, expressions, texts and stored extents changed): "
            "CtDraw's load + save in a fresh Graphics Builder, and again."
            % (LEGACY_FROM, len(LEGACY_NUMBERS), len(LEGACY_STRINGS)),
            _t("test_oracle_chain7", "test_special_objects"),
            _run_legacy_measure,
            sources=["FxMain/%s.ctg" % LEGACY_FROM],
            data={"project": "FxMain", "page": LEGACY_PAGE, "fresh": True},
        ),
        Unit(
            "chain7",
            "shipped_values",
            "The install's %s copied into the capture's %s: "
            "CtDraw's open + save of %s, and again in a fresh Graphics Builder. AVEVA content: "
            "it runs only with --out outside tests\\fixtures\\oracle and is never committed."
            % (
                ", ".join(
                    "%s\\%s" % (p, f) for p, f in [e[:2] for e in SHIPPED7_ELEMENTS] + list(SHIPPED7_EXTRA)
                ),
                SHIPPED7_PROJECT,
                ", ".join("%s.%s" % (os.path.splitext(f)[0], el) for _p, f, el in SHIPPED7_ELEMENTS),
            ),
            _t("test_oracle_chain7"),
            _run_shipped_values,
            data={"project": SHIPPED7_PROJECT, "fresh": True, "scratch_only": True},
        ),
        Unit(
            "chain7",
            "shipped_about",
            "The install's %s\\%s.ctg copied into FxMain: CtDraw's "
            "load + save at this session's DPI, and again in a fresh Graphics Builder. AVEVA "
            "content: it runs only with --out outside tests\\fixtures\\oracle and is never "
            "committed." % ABOUT_PAGE,
            _t("test_oracle_chain7"),
            _run_shipped_about,
            data={"project": "FxMain", "fresh": True, "scratch_only": True},
        ),
        Unit(
            "chain7",
            "self_template",
            "PageNewTemplate from %s %s@XGA, linked, saved under "
            "its own name (PageSaveAsEx, each spelling of %s, message boxes answered Yes): "
            "what CtDraw answers and writes; when it writes it, the template's load + save and "
            "a New Page from it in a fresh Graphics Builder. Then, from the library as it was, "
            "fxouter made from it and PageNewTemplate from fxouter saved as %s. Run it last, "
            "on its own, with a short --com-deadline."
            % (OUTER_STYLE, OUTER_BASE[0], " / ".join(SELF_NAMES), OUTER_BASE[0]),
            tpl,
            _run_self_template,
            sources=fx_style,
            data={"project": OUTER_PROJECT, "fresh": True},
        ),
        Unit(
            "chain7",
            "self_placeholder",
            "%s's %s as %s/%s captured it (a placeholder of "
            "fxnest.n1) copied into fxnest.ctm as %s by the tool: in a fresh Graphics Builder "
            "each, CtDraw's open + save of that element (what it places, what it answers, what "
            "it writes). Run it last, on its own, with a short --com-deadline."
            % (
                SELF_HOLDER_FROM[0],
                SELF_HOLDER_FROM[1],
                SELF_HOLDER_CAPTURE[0],
                SELF_HOLDER_CAPTURE[1],
                " and ".join(SELF_HOLDER_AS),
            ),
            holders + nest[1:],
            _run_self_placeholder,
            sources=["FxLib/fxnest.ctm", "FxLib/fxparts.ctm"],
            data={"project": "FxLib", "library": "fxnest.ctm", "fresh": True},
        ),
    ]


def compile_units() -> List[Unit]:
    return [
        Unit(
            "compile",
            proj,
            "The compiler's verdict (CtCmp32) on %s and what it includes." % proj,
            _t("test_sim_pagemodel"),
            _run_compile,
            data={"project": proj},
        )
        for proj in ("FxMain", "FxSim")
    ]


#: the golden needs of the test inventory that no unit captures, on purpose: test file ->
#: [(the need, why)]. A migrated test of one of them stays golden-skipped with this reason.
OUT_OF_SCOPE: Dict[str, List[Tuple[str, str]]] = {
    "tests/test_super_genies.py": [
        (
            "CtDraw's Attach Super Genies + save (bind guids, stamps, the button's auto width)",
            "the Attach Super Genies dialog has no COM route; filling it needs UI automation",
        ),
        (
            "A genie save that creates the super genie page",
            "no unit makes it on purpose; elements:FxLib/fxgenie.ctm saves fx_popper and keeps "
            "any page CtDraw makes under files/, so look there first",
        ),
    ],
    "tests/test_genie_dialog.py": [
        (
            "The genie dialog's rows and values for each instance",
            "the genie dialog is a modal window with no COM getter; reading it needs UI automation",
        ),
    ],
    "tests/test_genie_recompose_art.py": [
        (
            "The genie dialog's OK of a parameter change on a nested genie",
            "typing into the genie dialog and its OK (control 102) is UI automation, not COM",
        ),
    ],
    "tests/test_composer.py": [
        (
            "The composer's Instances, metadata.xml and cache.ctm for two or three option sets",
            "Paste Composite Genie is a WPF dialog, and CompositeGeniePlace takes parameters "
            "whose form is not pinned; SE.CtDraw.Composition.dll is a separate tool",
        ),
    ],
    "tests/test_colour_system.py": [
        (
            "A citect.pal written by Edit Favorite Colors",
            "a dialog with no COM route; it writes the install's Bin, which the run does not back up",
        ),
    ],
    "tests/test_parity_batch.py": [
        (
            "A flashing bitmap made by Import As Flashing",
            "a menu command with a file dialog; no COM route (the shipped one is checked in "
            "tests_corpus/test_parity_batch.py)",
        ),
    ],
    "tests/test_templates.py": [
        (
            "The template dialog's change of a template input",
            "the template dialog has no COM route; it needs UI automation",
        ),
    ],
    "tests/test_template_place.py": [
        (
            "A PROJECT.CIT written by Plant SCADA Studio",
            "our own projectcit writer is the source (the inventory allows it); Studio's "
            "rewrites at its start are taken into the baseline, not kept",
        ),
    ],
}


STEPS: Dict[str, Step] = {
    s.name: s
    for s in (
        Step(
            "pages",
            "PageOpen + PageSave of every page of FxMain, FxOther, FxSim and FxSa: the "
            ".ctg, the .ctF, the rows the save wrote, a PNG of the canvas for the "
            "render pages.",
            sorted(
                set(
                    PAGE_COMMON
                    + [t for ts in PAGE_FEEDS.values() for t in ts]
                    + _t("test_templates", "test_sa_workspace")
                )
            ),
            page_units,
        ),
        Step(
            "turns",
            "PositionRotate / PositionMirror* / PositionAt sequences on named objects, "
            "each ended by a PageSave.",
            sorted(set([x for t in TURNS for x in t[4]])),
            turn_units,
        ),
        Step(
            "pies", "The pie angle fold.", _t("test_arrange_correctness", "test_appearance_kinds"), pie_units
        ),
        Step(
            "draw",
            "Objects and input records CtDraw makes itself, and its copy + paste.",
            _t("test_factory_defaults", "test_input_records", "test_symbol_sets", "test_access_metadata_ai"),
            draw_units,
        ),
        Step(
            "place",
            "Genies and symbols CtDraw places itself (LibraryObjectPlaceEx).",
            _t(
                "test_open_load_rules",
                "test_super_genies",
                "test_genie_nesting",
                "test_nested_recompose",
                "test_elements",
                "test_symbol_workflows",
                "test_library_growth",
            ),
            place_units,
        ),
        Step(
            "newpage",
            "New Page from a fixture template, linked and not, and Cut Link of a page's template.",
            _t("test_template_place", "test_templates", "test_page_lifecycle"),
            newpage_units,
        ),
        # (each of its units starts in a fresh Graphics Builder, and the folders are put back
        # after it: its element saves never reach the elements step)
        Step(
            "chain",
            "An element edited in CtDraw (a text gained, mirrored and back; a template "
            "made from another, its inner one changed), then the load + save of the "
            "elements that nest it and a New Page from the outer template.",
            _t(
                "test_oracle_chain",
                "test_genie_nesting",
                "test_nested_recompose",
                "test_templates",
                "test_template_place",
            ),
            chain_units,
        ),
        # (as in chain)
        Step(
            "chain4",
            "A template's first own parameter and its element-form copy composed, a "
            "mirrored text resized, a turned and scaled placeholder genie, a genie "
            "without parameters pasted into one and gaining one, a placement value "
            "with a leading blank, Cut Link on a page whose outer template's inner one "
            "changed.",
            _t(
                "test_oracle_chain4",
                "test_genie_nesting",
                "test_nested_recompose",
                "test_templates",
                "test_template_place",
                "test_save_chain_geometry",
                "test_save_chain_reports3",
            ),
            chain4_units,
        ),
        # (as in chain)
        Step(
            "chain5",
            "A host saved while the genie it nests is stale, a parameter default that "
            "changes, the labels of an edited text and of multi-state and array texts, "
            "an element holding a placeholder, the handles when a page's template and "
            "genies both change, a shipped genie with older nested stamps, a binding "
            "the inner template lacks, an element-form copy's marker box, the Save "
            "Template warning, scaled groups, a genie placed into itself.",
            _t(
                "test_oracle_chain5",
                "test_genie_nesting",
                "test_nested_recompose",
                "test_templates",
                "test_template_place",
                "test_lang_labels",
                "test_placeholder_genies",
                "test_save_chain_stamps",
                "test_update_pages",
                "test_save_chain_geometry",
            ),
            chain5_units,
        ),
        # (as in chain)
        Step(
            "chain6",
            "Texts resized unevenly and mirrored back, an outer template with a "
            "parameter with a default and one without, a genie without parameters "
            "holding a pasted page-form copy that gains one, a page placing a host "
            "that is still stale, a page linked to a template holding a placeholder, "
            "a genie copy pasted into a genie element window.",
            _t(
                "test_oracle_chain6",
                "test_genie_nesting",
                "test_nested_recompose",
                "test_templates",
                "test_template_place",
                "test_placeholder_genies",
                "test_save_chain_geometry",
            ),
            chain6_units,
        ),
        # (as in chain)
        Step(
            "chain7",
            "Pages made and cut from a template holding a placeholder, a page copy of a "
            "genie without parameters pasted into a genie, a template update under an "
            "unbound parameter of the outer template, genies placed into a template "
            "with a parameter, leading blanks in a text and a binding, pages open while "
            "their template is saved, symbols of one DummyMarker, an array text with an "
            "unbound row, legacy Number and String extents, shipped value bags and "
            "texts, a template saved over itself, a genie holding a placeholder of "
            "itself.",
            _t(
                "test_oracle_chain7",
                "test_genie_nesting",
                "test_nested_recompose",
                "test_templates",
                "test_template_place",
                "test_placeholder_genies",
                "test_special_objects",
                "test_display_value",
                "test_update_pages",
                "test_save_chain_geometry",
                "test_elements",
                "test_symbol_workflows",
            ),
            chain7_units,
        ),
        # (last of the CtDraw steps: its saves change the elements CtDraw holds in memory)
        Step(
            "elements",
            "Open + save of every library element (symbols, genies, super genies, "
            "templates): each element's block and each library after.",
            sorted(set(ELEMENT_COMMON + [t for ts in LIBRARY_FEEDS.values() for t in ts])),
            element_units,
        ),
        Step(
            "compile",
            "The compiler's verdict on FxMain and FxSim.",
            _t("test_sim_pagemodel"),
            compile_units,
            builder=False,
        ),
    )
}

#: pages whose named roots the prepare step maps to ANs
NAMED_PAGES = sorted({("FxMain", t[1]) for t in TURNS} | {("FxMain", "FxSymbols"), ("FxMain", "FxMany")})
#: pages whose template root the prepare step maps to its AN (and the links it must keep)
TEMPLATE_PAGES = (("FxMain", CUT_LINK_PAGE),)


def all_units() -> List[Unit]:
    out = []
    for s in STEPS.values():
        out.extend(s.units())
    return out


def select_units(spec: Optional[str]) -> List[Unit]:
    """The units ``--steps`` names: comma separated ``step`` or ``step:unit`` patterns
    (fnmatch, without case); all when ``spec`` is empty."""
    units = all_units()
    if not spec:
        return units
    pats = [p.strip() for p in spec.split(",") if p.strip()]
    for p in pats:
        if p.split(":", 1)[0].lower() not in STEPS:
            raise Refused("No step named %r (see --list)." % p.split(":", 1)[0])
    out = []
    for u in units:
        for p in pats:
            step, _c, unit = p.partition(":")
            if u.step == step.lower() and (not unit or fnmatch.fnmatch(u.name.lower(), unit.lower())):
                out.append(u)
                break
    return out


def list_steps(out=_say) -> None:
    for s in STEPS.values():
        units = s.units()
        out("%s (%d unit%s): %s" % (s.name, len(units), "" if len(units) == 1 else "s", s.what))
        out("  feeds: %s" % ", ".join(s.feeds))
        for u in units:
            out("    %s: %s" % (u.name, u.what))
    out("Not captured, on purpose (a test of these stays golden-skipped):")
    for test, needs in sorted(OUT_OF_SCOPE.items()):
        for need, why in needs:
            out("  %s: %s (%s)" % (test, need, why))


# ============================================================================ prepare
#: the deterministic environment of the helper process (as tools/make_fixtures.py)
def prepare_env(work: str) -> dict:
    env = dict(os.environ)
    for k in ("CTG_LIB_PATH", "CTG_SCADA_BIN", "CTG_AVEVA_HELP", "CTG_LIB_SIBLINGS"):
        env.pop(k, None)
    env.update(
        {
            "PYTHONPATH": REPO,
            "CTG_FIXED_IDENTITY": "fixture-oracle",
            "CTG_TEXT_DPI": "96",
            "CTG_CODEPAGE": "1252",
            "CTG_SCADA_ROOT": FIXTURES,
            "CTG_SETTINGS_INI": os.path.join(work, "settings.ini"),
            "CTG_DATA_DIR": os.path.join(work, "data"),
            "CTG_NO_RUNTIME": "1",
            "CTG_NO_STUDIO": "1",
            "CTG_NO_SIM_ENGINE": "1",
            "CTG_NO_CTF_PRECHECK": "1",
            "CTG_NO_DISPLAY_CHECK": "1",
            "CTG_NO_ANIMATION": "1",
            "CTG_NO_DIALOG_REAP": "1",
            "QT_QPA_PLATFORM": "offscreen",
            "QT_QPA_FONTDIR": os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
        }
    )
    return env


def run_prepare(work: str, log=_say) -> dict:
    """Run :func:`prepare` in its own process (ctgedit reads the fixture pages there; the
    capture process never parses a page) and return its result."""
    os.makedirs(work, exist_ok=True)
    r = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "prepare", work],
        env=prepare_env(work),
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise Refused("The prepare step failed:\n%s%s" % (r.stdout[-2000:], r.stderr[-4000:]))
    log("Prepared the names and the capture pages in %s" % work)
    return json.loads(_read(os.path.join(work, "prepared.json")).decode("utf-8"))


def _payload_with_handle(po, handle: int) -> bytes:
    from ctgedit.format import writer

    pl = bytearray(writer.object_payload(po))
    an_end = 0x0C + struct.unpack_from("<H", pl, 0x0A)[0]
    struct.pack_into("<H", pl, an_end + 2, handle & 0xFFFF)
    return bytes(pl)


def prepare(work: str) -> dict:
    """Map the named roots of NAMED_PAGES to their ANs and write the capture pages (made
    with ctgedit under a fixed identity) into ``work/inputs``."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G, records as R
    from ctgedit.format import template_place as TP
    from ctgedit.format.symbols import reference_from_object

    names: Dict[str, dict] = {}
    for proj, page in NAMED_PAGES:
        path = os.path.join(FIXTURES, "User", SOURCE_OF[proj], page + ".ctg")
        doc = EditableDocument.open(path)
        objs = doc.parsed.page.objects
        found = {}
        for i in range(len(objs)):
            n = doc._stored_name(i)
            if not n or objs[i].raw_payload is not None:
                continue
            h = G.get_handle(objs[i])
            found.setdefault(
                n,
                {
                    "index": i,
                    "handle": h,
                    "an": h + 1,
                    "root": doc.page.objects[i].parent is None,
                    "type_code": objs[i].type_code,
                },
            )
        names["%s/%s" % (proj, page)] = {"sha1": file_sha1(path), "names": found}
    templates: Dict[str, dict] = {}
    for proj, page in TEMPLATE_PAGES:
        path = os.path.join(FIXTURES, "User", SOURCE_OF[proj], page + ".ctg")
        doc = EditableDocument.open(path)
        i, ref = doc.template_reference()
        if i is None:
            continue
        objs = doc.parsed.page.objects
        h = G.get_handle(objs[i])
        # the elements the page's other roots link: a Cut Link of the template keeps them
        keep = []
        for k, po in enumerate(objs):
            if k == i or doc.page.objects[k].parent is not None:
                continue
            with contextlib.suppress(Exception):
                other = reference_from_object(po)
                if other is not None:
                    keep.append("%s.%s.<%s" % (other.library, other.element, other.ext))
        templates["%s/%s" % (proj, page)] = {
            "sha1": file_sha1(path),
            "index": i,
            "handle": h,
            "an": h + 1,
            "reference": "%s.%s.<%s" % (ref.library, ref.element, ref.ext),
            "keep": sorted(set(keep)),
        }
    # the members of the element the chain units edit (their ANs in its window)
    from ctgedit.format.element import open_element

    elements: Dict[str, dict] = {}
    proj, lib = CHAIN_LIB
    path = os.path.join(FIXTURES, "User", SOURCE_OF[proj], lib)
    pe = open_element(path, CHAIN_EDITED)
    recs = []
    for i, po in enumerate(pe.tree[1:], 1):
        h = G.get_handle(po)
        recs.append({"index": i, "handle": h, "an": h + 1, "type_code": po.type_code})
    elements["%s/%s/%s" % (proj, lib, CHAIN_EDITED)] = {"sha1": file_sha1(path), "records": recs}
    inputs = os.path.join(work, "inputs")
    os.makedirs(inputs, exist_ok=True)

    def blank(name):
        path = os.path.join(inputs, name + ".ctg")
        data = TP.new_page_bytes(1024, 768, background=(0xD8D8D8, 0xD8D8D8))
        return EditableDocument.from_bytes(data, path)

    made = {}
    for name in ("FxOracleDraw", "FxOraclePlace"):
        doc = blank(name)
        doc.save()
        made[name] = "a blank 1024 x 768 page"
    doc = blank("FxOracleGaps")
    for k in range(6):
        i = doc.create_object("rectangle", rect=(40 + 70 * k, 40, 100 + 70 * k, 90), colour=0x3060A0)
        doc.set_object_name(i, "FxGap%02d" % k)
    objs = list(doc.parsed.page.objects)
    doc._structural(
        "handles", [R.to_po(_payload_with_handle(po, h)) for po, h in zip(objs, (0, 1, 2, 4, 6, 13))]
    )
    doc.save()
    made["FxOracleGaps"] = "six rectangles FxGap00 .. FxGap05 with the handles 0, 1, 2, 4, 6, 13"
    doc = blank("FxOraclePies")
    for k, a in enumerate(PIE_ANGLES):
        b = PIE_ANGLES[(k + 1) % len(PIE_ANGLES)]
        x, y = 30 + (k % 6) * 160, 30 + (k // 6) * 160
        i = doc.create_object("ellipse", rect=(x, y, x + 120, y + 120), colour=0xA02040)
        doc.set_ellipse(i, style=1, start=0, end=90)
        doc.set_object_name(i, "FxPie%02d" % k)

        def raw_angles(po, a=a, b=b):
            tail = G.parse_shape_tail(po)
            body = bytearray(po.body_raw)
            struct.pack_into("<2h", body, tail["angles_off"], a, b)
            po.body_raw = bytes(body)
            return True

        doc._body_edit("ellipse", i, raw_angles)
    doc.save()
    made["FxOraclePies"] = "pies FxPie00 .. FxPie%02d, angles (start, end) stored unfolded: %s" % (
        len(PIE_ANGLES) - 1,
        ", ".join("(%d, %d)" % (a, PIE_ANGLES[(k + 1) % len(PIE_ANGLES)]) for k, a in enumerate(PIE_ANGLES)),
    )
    result = {
        "names": names,
        "templates": templates,
        "elements": elements,
        "inputs": {
            n: {"file": "inputs/%s.ctg" % n, "what": w, "sha1": file_sha1(os.path.join(inputs, n + ".ctg"))}
            for n, w in made.items()
        },
    }
    write_json(os.path.join(work, "prepared.json"), result)
    return result


# ============================================================================ the capture
class ComClock:
    """The COM call that runs now: (name, start time) or None (the watchdog reads it)."""

    def __init__(self):
        self.call: Optional[Tuple[str, float]] = None


class LoggedCom:
    """The Graphics Builder's IDispatch with every call written to the COM log (and, with
    a ``clock``, the running call noted for the watchdog)."""

    def __init__(self, com, log, clock: Optional[ComClock] = None):
        object.__setattr__(self, "_com", com)
        object.__setattr__(self, "_log", log)
        object.__setattr__(self, "_clock", clock)

    def __getattr__(self, name):
        try:
            attr = getattr(self._com, name)
        except Exception as e:  # noqa: BLE001
            self._log("COM get %s -> error %s" % (name, e))
            raise
        if not callable(attr):
            self._log("COM get %s -> %r" % (name, attr))
            return attr

        def call(*args):
            t0 = time.time()
            clock = self._clock
            if clock is not None:
                clock.call = (name, t0)
            try:
                r = attr(*args)
            except Exception as e:  # noqa: BLE001
                self._log("COM %s%r -> error %s (%.2f s)" % (name, args, e, time.time() - t0))
                raise
            finally:
                if clock is not None:
                    clock.call = None
            self._log("COM %s%r -> %r (%.2f s)" % (name, args, r, time.time() - t0))
            return r

        return call


class StopFlag:
    """Ctrl+C once: stop after the current unit; twice: at once."""

    def __init__(self):
        self.requested = False

    def handler(self, _sig, _frame):
        if self.requested:
            raise KeyboardInterrupt
        self.requested = True
        _say("Stopping after this unit (Ctrl+C again to stop now). The install is restored either way.")


#: what the console says when CtDraw shows a dialog that is not a message box
HANG_ADVICE = (
    "If the capture waits behind it, close it in the Graphics Builder. The "
    "watchdog ends CtDraw after %d s; if you end python instead, run "
    "'fixture_oracle.py restore --stop-studio'."
)


def _open_builder():
    """The Graphics Builder the capture drives: GraphicsBuilder(visible=True).open()."""
    from ctgedit.backend.graphics_builder import GraphicsBuilder

    return GraphicsBuilder(visible=True).open()


def kill_ctdraw(log) -> None:
    """End CtDraw32 (a COM call blocked behind it then fails and the run goes on)."""
    subprocess.run(["taskkill", "/F", "/IM", "CtDraw32.exe"], capture_output=True, text=True)
    log("Ended CtDraw32.exe.")


class DialogWatch:
    """A thread that answers the Graphics Builder's message boxes."""

    def __init__(
        self,
        log,
        clock: Optional[ComClock] = None,
        deadline: float = 600.0,
        kill: Optional[Callable[[Callable[[str], None]], None]] = None,
    ):
        import threading

        self.log = log
        self.clock = clock
        self.deadline = deadline
        self.kill = kill or kill_ctdraw
        self.prefer = "No"
        self.seen: List[str] = []
        self.dialogs: List[str] = []
        self._told = set()
        #: answer a dialog that is not a message box with its OK (the dialogs open when it
        #: was set, in ``ok_known``, are left alone)
        self.press_ok = False
        self.ok_known: set = set()
        self.answered: List[dict] = []
        self._killed: Optional[Tuple[str, float]] = None
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="dialog-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        self._thread.join(timeout=5.0)

    def take(self) -> List[str]:
        out, self.seen = self.seen, []
        return out

    def check_deadline(self, now: Optional[float] = None) -> bool:
        """End CtDraw when the running COM call is older than the deadline (once per
        call). True when it did."""
        call = self.clock.call if self.clock is not None else None
        if not call or not self.deadline or call == self._killed:
            return False
        now = time.time() if now is None else now
        if now - call[1] < self.deadline:
            return False
        self._killed = call
        self.log(
            "The COM call %s has waited %.0f s (the deadline is %.0f s). Dialogs seen: "
            "%s. Ending CtDraw so the call fails; the next unit gets a fresh one."
            % (call[0], now - call[1], self.deadline, "; ".join(self.dialogs[-5:]) or "none")
        )
        self.kill(self.log)
        return True

    def _press_ok(self, winui, h, kids) -> dict:
        """Answer a dialog with its OK button (WM_COMMAND IDOK when it has none): its
        title and the texts of its controls, for the unit's record."""
        title = winui.text(h)
        texts = [k["text"] for k in kids if (k["text"] or "").strip()][:60]
        btn = winui.find_button(kids, "OK")
        if btn:
            winui.click(btn)
            used = "OK"
        else:
            import win32con
            import win32gui

            win32gui.PostMessage(h, win32con.WM_COMMAND, win32con.IDOK, 0)
            used = "IDOK"
        self.log("A CtDraw dialog %r, answered %s (texts: %s)" % (title, used, texts))
        return {"title": title, "texts": texts, "answered": used}

    def _loop(self) -> None:
        from ctgedit.backend import winui

        while not self._halt.wait(1.0):
            try:
                for pid in winui.pids_of("CtDraw32.exe"):
                    for h in winui.windows_of(pid):
                        kids = winui.children(h)
                        if winui.is_message_box(kids):
                            text = winui.message_text(kids)
                            used = winui.dismiss_message_box(h, kids, prefer=self.prefer)
                            self.seen.append("%s [%s]" % (text, used))
                            self.log("A CtDraw message box, answered %s: %s" % (used, text))
                        elif self.press_ok and h not in self.ok_known:
                            self.ok_known.add(h)
                            self.answered.append(self._press_ok(winui, h, kids))
                        elif h not in self._told:
                            self._told.add(h)
                            title = winui.text(h)
                            self.dialogs.append(repr(title))
                            self.log("A CtDraw dialog is open: %r. %s" % (title, HANG_ADVICE % self.deadline))
            except Exception as e:  # noqa: BLE001
                self.log("Dialog watch: %s" % e)
            try:
                self.check_deadline()
            except Exception as e:  # noqa: BLE001
                self.log("Watchdog: %s" % e)


#: files the capture never collects (lock and scratch files)
IGNORED_SUFFIXES = (".lck", ".tmp", ".bak", "~")


class Oracle:
    """The COM side: drives the Graphics Builder through the units and writes the oracle."""

    def __init__(
        self,
        change: InstallChange,
        out_root: str,
        prepared: dict,
        prep_dir: str,
        shots: str = "listed",
        stop: Optional[StopFlag] = None,
        raw_tables: bool = False,
        com_deadline: float = 600.0,
        restart: str = "studio",
    ):
        self.change = change
        self.clock = ComClock()
        self.com_deadline = com_deadline
        self.inst = change.inst
        self.out_root = os.path.abspath(out_root)
        self.prepared = prepared
        self.prep_dir = prep_dir
        self.shots = shots
        self.raw_tables = raw_tables
        #: the settle time after an open or a save, in seconds per second (tests: 0)
        self.pause = 1.0
        self.stop = stop or StopFlag()
        self.gb = None
        self.com = None
        #: what a restart ends: 'studio' (Studio and its CtDraw32) or 'ctdraw' (CtDraw32)
        self.restart = restart
        #: makes and opens the GraphicsBuilder (tests: a stand in)
        self.builder_factory: Callable[[], object] = _open_builder
        #: ends the Graphics Builder for a restart (default: :meth:`_end_builder`)
        self.end_builder: Callable[[], None] = self._end_builder
        #: the builder attached now has opened something (a page, an element, a new page)
        self.used = False
        #: the library elements it has opened in a window: (library, element), lower case
        self.opened: set = set()
        #: the CtDraw32 processes that ran when it attached (gone: the watchdog ended it)
        self.pids: set = set()
        self.restarts = 0
        self.baseline: Dict[str, bytes] = {}
        self.cache: Dict[str, object] = {}
        self.version = "unknown"
        self.today = _dt.date.today().isoformat()
        self.failures: List[str] = []
        self.ran: Dict[str, str] = {}  # unit key -> status, this run
        self.watch: Optional[DialogWatch] = None
        self._com_log = open(os.path.join(change.run_dir, "com.log"), "a", encoding="utf-8")
        self.manifest_path = os.path.join(self.out_root, "manifest.json")
        old = _read(self.manifest_path)
        self.manifest = json.loads(old.decode("utf-8")) if old else {}
        self.manifest.setdefault("files", [])
        self.manifest.setdefault("units", {})

    # ------------------------------------------------------------------ logs
    def log(self, text: str) -> None:
        line = "%s %s" % (_dt.datetime.now().strftime("%H:%M:%S"), text)
        self._com_log.write(line + "\n")
        self._com_log.flush()
        if not text.startswith("COM "):
            _say(line)

    # ------------------------------------------------------------------ the folders
    def _walk(self) -> Dict[str, str]:
        out = {}
        for t in TARGETS:
            base = os.path.join(self.inst.user, t)
            for d, _sub, files in os.walk(base):
                for f in files:
                    p = os.path.join(d, f)
                    out[os.path.relpath(p, self.inst.user).replace("\\", "/")] = p
        return out

    def take_baseline(self) -> None:
        self.baseline = {rel: _read(p) for rel, p in self._walk().items()}

    def changes(self) -> List[Tuple[str, Optional[bytes], Optional[bytes]]]:
        now = self._walk()
        out = []
        for rel in sorted(set(now) | set(self.baseline)):
            before = self.baseline.get(rel)
            after = _read(now[rel]) if rel in now else None
            if before != after:
                out.append((rel, before, after))
        return out

    def adopt_changes(self, why: str, keep: Iterable[str] = ()) -> None:
        """Take what changed since the baseline into it (Studio's start writes some files),
        but the files of ``keep`` (a unit's own changes so far, during a restart)."""
        keep = set(keep)
        for rel, _b, after in self.changes():
            if rel in keep:
                continue
            self.log("%s: %s %s" % (why, rel, "removed" if after is None else "written"))
            if after is None:
                self.baseline.pop(rel, None)
            else:
                self.baseline[rel] = after

    def reset(self) -> None:
        """The Fx folders back to the baseline: changed files rewritten, new files removed."""
        for rel, before, _after in self.changes():
            p = os.path.join(self.inst.user, rel.replace("/", os.sep))
            try:
                if before is None:
                    os.remove(p)
                else:
                    _write(p, before)
            except OSError as e:
                self.log("Reset %s: %s" % (rel, e))

    # ------------------------------------------------------------------ outputs
    def _scrub(self, obj):
        if isinstance(obj, str):
            s = obj
            for path, token in (
                (self.inst.user, "<FX>"),
                (self.inst.root, "<FX_INSTALL>"),
                (self.change.run_dir, "<RUN>"),
                (REPO, "<REPO>"),
            ):
                for form in (path, path.replace("\\", "/")):
                    low = s.lower()
                    k = low.find(form.lower())
                    while k >= 0:
                        s = s[:k] + token + s[k + len(form) :]
                        low = s.lower()
                        k = low.find(form.lower(), k + len(token))
            return s
        if isinstance(obj, dict):
            return {self._scrub(k): self._scrub(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._scrub(v) for v in obj]
        return obj

    def _holds_path(self, data: bytes) -> bool:
        low = data.lower()
        for p in {self.inst.user, self.inst.root}:
            for form in (p, p.replace("\\", "/")):
                if form.lower().encode("cp1252", "replace") in low or form.lower().encode("utf-16-le") in low:
                    return True
        return False

    def unit_dir(self, unit: Unit) -> str:
        return os.path.join(self.out_root, unit.step, *[safe_name(p) for p in unit.name.split("/")])

    def emit(self, unit: Unit, rel: str, data: bytes, what: str, source: Optional[str] = None) -> None:
        """Write one output file of ``unit`` and its manifest entry. ``source`` = the fixture
        file (User relative, as in the fixture install) or 'inputs/<name>.ctg'."""
        path = os.path.join(self.unit_dir(unit), rel.replace("/", os.sep))
        out_rel = os.path.relpath(path, self.out_root).replace("\\", "/")
        entry = {
            "file": out_rel,
            "step": unit.step,
            "unit": unit.name,
            "what": what,
            "tests": unit.feeds,
            "ctdraw": self.version,
            "captured": self.today,
        }
        if source:
            entry["source"] = source
            entry["source_sha1"] = self._source_sha1(source)
        if not rel.endswith(".json") and self._holds_path(data):
            entry["withheld"] = "the file holds a path of the install"
            self.log("Withheld %s: it holds a path of the install." % out_rel)
        else:
            _write(path, data)
            entry.update(sha1=sha1(data), size=len(data))
        self.manifest["files"].append(entry)

    def emit_json(self, unit: Unit, rel: str, obj, what: str, source: Optional[str] = None) -> None:
        self.emit(
            unit,
            rel,
            (json.dumps(self._scrub(obj), indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode(
                "utf-8"
            ),
            what,
            source,
        )

    def _source_sha1(self, source: str) -> Optional[str]:
        if source.startswith("inputs/"):
            return file_sha1(os.path.join(self.prep_dir, source.replace("/", os.sep)))
        return file_sha1(os.path.join(FIXTURES, "User", source.replace("/", os.sep)))

    def collect(self, unit: Unit, skip: Iterable[str] = ()) -> None:
        """Every file the unit changed or made in the Fx folders (but ``skip``): the bytes
        under files/, a changed table as its rows and an index as its sha1 in rows.json
        (their bytes too with ``raw_tables``: a page save rewrites FxMain's 750 KB pgdynobj),
        the removed files listed."""
        skip = {s.lower() for s in skip}
        rows, indexes, removed = {}, {}, []
        for rel, before, after in self.changes():
            if rel.lower() in skip or rel.endswith("/" + MARKER) or rel.lower().endswith(IGNORED_SUFFIXES):
                continue
            if after is None:
                removed.append(rel)
                continue
            proj = rel.split("/", 1)[0]
            src = "%s/%s" % (SOURCE_OF.get(proj, proj), rel.split("/", 1)[1]) if before is not None else None
            low = rel.lower()
            if low.endswith(".dbf"):
                rows[rel] = dbf_diff(before, after)
            elif low.endswith(".ndx"):
                indexes[rel] = {"sha1": sha1(after), "size": len(after)}
            if self.raw_tables or not low.endswith((".dbf", ".ndx")):
                self.emit(
                    unit,
                    "files/" + rel,
                    after,
                    "A file CtDraw's work in this unit %s." % ("changed" if before is not None else "made"),
                    src,
                )
        if rows or removed or indexes:
            self.emit_json(
                unit,
                "rows.json",
                {"tables": rows, "indexes": indexes, "removed": removed},
                "The rows the unit added or changed in each table, the indexes it "
                "changed (sha1) and the files it removed.",
            )

    def finish_unit(self, unit: Unit, status: str, note: str = "") -> None:
        dialogs = self.watch.take() if self.watch is not None else []
        self.ran[unit.key] = status
        self.manifest["units"][unit.key] = {
            "status": status,
            "what": unit.what,
            "tests": unit.feeds,
            "note": self._scrub(note),
            "dialogs": self._scrub(dialogs),
            "captured": self.today,
            "ctdraw": self.version,
        }
        self.write_manifest()
        with contextlib.suppress(Exception):
            self.change.note_seen()

    def write_manifest(self) -> None:
        self.manifest["files"].sort(key=lambda e: e["file"])
        self.manifest.update(
            tool=TOOL,
            tool_version=TOOL_VERSION,
            fixtures="tests/fixtures/FxInstall",
            out_of_scope={t: [{"need": n, "why": w} for n, w in needs] for t, needs in OUT_OF_SCOPE.items()},
            note="Captured by tools/live/fixture_oracle.py (docs/TESTING.md). "
            "<FX> is the install's User folder.",
        )
        write_json(self.manifest_path, self.manifest)

    # ------------------------------------------------------------------ COM lifecycle
    def start(self) -> None:
        self.version = ctdraw_version()
        self.log(
            "Starting Plant SCADA Studio and attaching to its Graphics Builder (CtDraw %s)." % self.version
        )
        self.watch = DialogWatch(self.log, self.clock, self.com_deadline)
        self.watch.start()
        self._attach()
        self.adopt_changes("Studio's start")

    def _attach(self) -> None:
        """Attach to a Graphics Builder (GraphicsBuilder.open starts Studio when it does not
        run, and Studio starts its own CtDraw32): a fresh one, nothing opened yet."""
        self.gb = self.builder_factory()
        self.com = LoggedCom(self.gb.com, self.log, self.clock)
        self.used = False
        self.opened = set()
        self.cache.pop("project", None)
        self.wait(3.0)
        self.pids = self._ctdraw_pids()

    def _detach(self) -> None:
        """Let the attached Graphics Builder go (its page closed first). GraphicsBuilder's
        close of a builder it attached to (Studio's own) only parks the proxy: the CtDraw32
        process lives on, so it is not a fresh builder yet."""
        if self.gb is not None:
            with contextlib.suppress(Exception):
                self.com.PageClose()
            with contextlib.suppress(Exception):
                self.gb.close()
        self.gb = None
        self.com = None

    def _end_builder(self) -> None:
        """End Studio and the Graphics Builder for a restart."""
        if self.restart == "ctdraw":
            kill_ctdraw(self.log)
        else:
            stop_studio(self.log)

    def _ctdraw_pids(self) -> set:
        return set((self.change.processes() or {}).get("CtDraw32.exe", []))

    def builder_gone(self) -> bool:
        """Whether the CtDraw32 the run attached to has ended (the watchdog ends one whose
        COM call waits too long): every later call of that builder would fail."""
        return bool(self.pids) and not (self.pids & self._ctdraw_pids())

    def fresh_builder(self, why: str, force: bool = False) -> bool:
        """A fresh Graphics Builder, unless the current one will do."""
        if not self.used and not force:
            return False
        self.log("A fresh Graphics Builder: %s." % why)
        pending = {rel for rel, _b, _a in self.changes()}
        old = self._ctdraw_pids()
        self._detach()
        self.end_builder()
        deadline = time.time() + 20.0 * self.pause
        left = old & self._ctdraw_pids()
        while left and time.time() < deadline:
            time.sleep(0.5)
            left = old & self._ctdraw_pids()
        if left:
            raise BuilderLost(
                "CtDraw32 (pid %s) still runs: no fresh Graphics Builder" % ", ".join(map(str, sorted(left)))
            )
        try:
            self._attach()
        except Exception as e:  # noqa: BLE001
            raise BuilderLost("no fresh Graphics Builder attached: %s" % e) from e
        self.adopt_changes("The Graphics Builder's restart", keep=pending)
        self.restarts += 1
        return True

    def fresh_reason(self, unit: Unit, prev: Optional[Unit]) -> Optional[str]:
        """Why ``unit`` needs a fresh Graphics Builder (None when it does not): the builder
        has ended, the first unit of a step that drives the builder, a unit that asks for
        one (``fresh``: a library with nested genies, the nested placement), a unit whose
        source names, places or opens an element this builder has opened."""
        step = STEPS.get(unit.step)
        if step is not None and not step.builder:
            return None
        if self.builder_gone():
            return "the Graphics Builder has ended"
        if prev is not None and prev.step != unit.step:
            return "the %s step starts" % unit.step
        if unit.data.get("fresh"):
            return "%s asks for one" % unit.key
        names = []
        for src in unit.sources:
            data = _read(os.path.join(FIXTURES, "User", src.replace("/", os.sep)))
            if data:
                names += linked_elements(data, self.opened)
        for action in unit.data.get("actions", ()):
            if action[0] == "place" and (action[2].lower(), action[3].lower()) in self.opened:
                names.append("%s.%s" % (action[2].lower(), action[3].lower()))
        # (an element the unit opens itself: append opens fxmany.fx_last_big, which the
        # fxmany.ctl unit opened and saved)
        lib, el = unit.data.get("library"), unit.data.get("element")
        if lib and el:
            key = (os.path.splitext(lib)[0].lower(), el.lower())
            if key in self.opened:
                names.append("%s.%s" % key)
        if names:
            return "%s names %s, which this builder has opened" % (unit.key, ", ".join(sorted(set(names))))
        return None

    def close(self) -> None:
        self._detach()
        if self.watch is not None:
            self.watch.stop()
        with contextlib.suppress(Exception):
            self._com_log.close()

    def run_units(self, units: Sequence[Unit]) -> None:
        prev = None
        for n, unit in enumerate(units, 1):
            if self.stop.requested:
                self.log("Stopped before %s." % unit.key)
                break
            self.log("[%d/%d] %s" % (n, len(units), unit.key))
            shutil.rmtree(self.unit_dir(unit), ignore_errors=True)
            self.manifest["files"] = [
                e for e in self.manifest["files"] if not (e["step"] == unit.step and e["unit"] == unit.name)
            ]
            self.reset()
            why, prev = self.fresh_reason(unit, prev), unit
            try:
                if why:
                    self.fresh_builder(why, force=self.builder_gone())
                unit.run(self, unit)
            except KeyboardInterrupt:
                self.finish_unit(unit, "stopped")
                raise
            except BuilderLost as e:
                self.log("FAILED %s: %s. The run stops." % (unit.key, e))
                self.failures.append(unit.key)
                self.finish_unit(unit, "failed", "%s: %s" % (type(e).__name__, e))
                raise
            except Exception as e:  # noqa: BLE001
                self.log("FAILED %s: %s" % (unit.key, e))
                self.log(traceback.format_exc())
                self.failures.append(unit.key)
                self.finish_unit(unit, "failed", "%s: %s" % (type(e).__name__, e))
            else:
                self.finish_unit(unit, "captured")
            finally:
                with contextlib.suppress(Exception):
                    self.com.PageClose()
                self.reset()

    # ------------------------------------------------------------------ COM helpers
    def wait(self, seconds: float) -> None:
        """Let CtDraw settle after an open or a save."""
        if self.pause:
            time.sleep(seconds * self.pause)

    def project(self, name: str) -> None:
        """ProjectSelect when the project changes (Studio follows the selection)."""
        if self.cache.get("project") == name:
            return
        try:
            self.com.ProjectSelect(name)
            self.cache["project"] = name
        except Exception:  # noqa: BLE001
            self.cache.pop("project", None)

    def page_name(self) -> str:
        try:
            return str(self.com.PageName())
        except Exception:  # noqa: BLE001
            return ""

    def open_page(self, project: str, page: str) -> None:
        with contextlib.suppress(Exception):
            self.com.PageClose()
        self.project(project)
        self.used = True
        self.com.PageOpen(project, page)
        self.wait(1.0)
        got = self.page_name()
        if page.lower() not in got.lower():
            raise UnitFailed("PageOpen(%s, %s) left %r active" % (project, page, got))

    def open_element(self, project: str, library: str, element: str) -> int:
        """PageOpenEx of an element; the page type is tried 0 .. 8 (the one that worked for
        the library's extension first) and checked through PageName. Returns the type."""
        stem, ext = os.path.splitext(library)
        key = "page type " + ext.lower()
        order = list(range(9))
        if key in self.cache:
            order.remove(self.cache[key])
            order.insert(0, self.cache[key])
        base = element.split("@")[0] if element.startswith("!") else element
        names = [element] + ([base] if base != element else [])
        self.project(project)
        self.used = True
        # (CtDraw holds it by library name from now on: fresh_reason reads this)
        self.opened.add((stem.lower(), element.lower()))
        for name in names:
            for ptype in order:
                with contextlib.suppress(Exception):
                    self.com.PageClose()
                try:
                    self.com.PageOpenEx(project, stem, name, ptype)
                except Exception:  # noqa: BLE001
                    continue
                got = self.page_name().lower()
                if base.split("@")[0].lower() in got:
                    self.cache[key] = ptype
                    self.wait(0.5)
                    return ptype
        raise UnitFailed("PageOpenEx found no page type for %s.%s (%s)" % (stem, element, ext))

    def save(self) -> None:
        with contextlib.suppress(Exception):
            self.com.PageSelectObject(0)  # (a selected set is saved 5 px wider)
        self.com.PageSave()
        self.wait(0.5)

    def select(self, unit: Unit, names: Sequence[str]) -> List[int]:
        info = self.prepared["names"].get("%s/%s" % (unit.data["project"], unit.data["page"]))
        if not info:
            raise UnitFailed("prepare mapped no names on %s" % unit.data["page"])
        ans = []
        for n in names:
            hit = info["names"].get(n)
            if hit is None:
                raise UnitFailed("%s has no object named %s" % (unit.data["page"], n))
            if not hit["root"]:
                raise UnitFailed("%s is not a root object (PageSelectObject takes a root)" % n)
            ans.append(hit["an"])
        for k, an in enumerate(ans):
            (self.com.PageSelectObject if k == 0 else self.com.PageSelectObjectAdd)(an)
        got = None
        with contextlib.suppress(Exception):
            got = int(self.com.AttributeAN())
        if got is not None and got not in ans:
            raise UnitFailed("selected AN %d, wanted %s" % (got, ans))
        with contextlib.suppress(Exception):
            self.com.AttributeClass()  # (logged; never AttributeText*)
        return ans

    def read(self, project: str, name: str) -> Optional[bytes]:
        p = _find_file(os.path.join(self.inst.user, project), name)
        return _read(p) if p else None

    def put_input(self, project: str, page: str) -> bytes:
        data = _read(os.path.join(self.prep_dir, "inputs", page + ".ctg"))
        if data is None:
            raise UnitFailed("prepare made no %s" % page)
        _write(os.path.join(self.inst.user, project, page + ".ctg"), data)
        return data

    def shot(self) -> Optional[bytes]:
        """A PNG of the active page's canvas in CtDraw."""
        if self.shots == "none":
            return None
        try:
            import io
            import win32con
            import win32gui
            import ctdraw_inspect as ci

            fr = ci.main_frame()
            if fr is None:
                self.log("No CtDraw frame to shoot.")
                return None
            frame = fr[0]
            win32gui.ShowWindow(frame, win32con.SW_MAXIMIZE)
            ci.ensure_front(frame)
            client = win32gui.FindWindowEx(frame, 0, "MDIClient", None)
            active = 0
            if client:
                active = win32gui.SendMessage(client, win32con.WM_MDIGETACTIVE, 0, 0)
            if active:
                win32gui.SendMessage(client, win32con.WM_MDIMAXIMIZE, active, 0)
            self.wait(1.5)
            canvases = []

            def found(h, _):
                if win32gui.GetClassName(h) == "ctDraw.canvas" and win32gui.IsWindowVisible(h):
                    canvases.append(h)
                return True

            win32gui.EnumChildWindows(frame, found, None)
            target = active if active in canvases else (canvases[0] if canvases else 0)
            if not target:
                self.log("No CtDraw canvas to shoot.")
                return None
            bmp = os.path.join(self.change.run_dir, "shot.bmp")
            window = win32gui.GetWindowRect(target)
            origin = win32gui.ClientToScreen(target, (0, 0))
            _l, _t, cw, ch = win32gui.GetClientRect(target)
            ci.shoot(target, bmp)
            try:
                from PIL import Image

                img = Image.open(bmp).convert("RGB")
                box = client_box(window, origin, (cw, ch))
                if (
                    box[0] < 0
                    or box[1] < 0
                    or box[2] > img.width
                    or box[3] > img.height
                    or box[2] <= box[0]
                    or box[3] <= box[1]
                ):
                    self.log(
                        "The canvas's client area %r is not inside its grab (%d x %d)."
                        % (box, img.width, img.height)
                    )
                    return None
                buf = io.BytesIO()
                img.crop(box).save(buf, "PNG")
                return buf.getvalue()
            finally:
                with contextlib.suppress(OSError):
                    os.remove(bmp)
        except Exception as e:  # noqa: BLE001
            self.log("Screenshot failed: %s" % e)
            return None

    def collect_page(self, unit: Unit, project: str, page: str, source: Optional[str]) -> None:
        data = self.read(project, page + ".ctg")
        if data is None:
            raise UnitFailed("%s.ctg is gone after the save" % page)
        self.emit(unit, "page.ctg", data, "%s.ctg as CtDraw saved it." % page, source)
        ctf = self.read(project, page + ".ctF")
        if ctf is not None:
            self.emit(unit, "page.ctF", ctf, "The .ctF CtDraw wrote with it.", source)
        self.collect(unit, skip={"%s/%s.ctg" % (project, page), "%s/%s.ctf" % (project, page)})

    def place(
        self,
        project_page: Tuple[str, str],
        lib_project: str,
        lib: str,
        element: str,
        ext: str,
        x: int,
        y: int,
        props,
    ) -> int:
        """LibraryObjectPlaceEx with its type tried 0 .. 4 (cached per extension), each try
        saved and checked: the page's link table must name ``lib.element.<ext``."""
        project, page = project_page
        key = "place type " + ext
        order = list(range(5))
        if key in self.cache:
            order.remove(self.cache[key])
            order.insert(0, self.cache[key])
        want = ("%s.%s.<%s" % (lib, element, ext)).lower().encode()
        start = self.read(project, page + ".ctg")
        for t in order:
            try:
                self.com.LibraryObjectPlaceEx(lib_project, lib, element, t, ext == "ctl", x, y)
                for k, v in props:
                    try:
                        self.com.LibraryObjectPutProperty(k, v)
                    except Exception as e:  # noqa: BLE001
                        self.log("LibraryObjectPutProperty(%s, %s): %s" % (k, v, e))
                self.save()
            except Exception:  # noqa: BLE001
                self._reopen(project, page, start)
                continue
            if want in (self.read(project, page + ".ctg") or b"").lower():
                self.cache[key] = t
                return t
            self._reopen(project, page, start)
        raise UnitFailed("LibraryObjectPlaceEx placed no %s.%s" % (lib, element))

    def _reopen(self, project: str, page: str, data: Optional[bytes]) -> None:
        with contextlib.suppress(Exception):
            self.com.PageClose()
        if data is not None:
            _write(os.path.join(self.inst.user, project, page + ".ctg"), data)
        self.open_page(project, page)

    def menu_command(self, command_id: int) -> None:
        """Run a CtDraw menu command: WM_COMMAND with its id posted to the frame window."""
        import win32con
        import win32gui
        import ctdraw_inspect as ci

        fr = ci.main_frame()
        if fr is None:
            raise UnitFailed("no CtDraw frame for menu command %d" % command_id)
        self.log("Menu command %d posted to the CtDraw frame." % command_id)
        win32gui.PostMessage(fr[0], win32con.WM_COMMAND, command_id, 0)

    def menu_checked(self, command_id: int) -> Optional[bool]:
        """Whether CtDraw's menu item ``command_id`` is ticked, or None when it is not found.
        The menus are filled as hovering fills them (WM_INITMENU / WM_INITMENUPOPUP, as
        tools/ctdraw_inspect.py --enumerate does): nothing runs."""
        try:
            import win32gui
            import ctdraw_inspect as ci

            fr = ci.main_frame()
            if fr is None:
                return None
            hmenu = win32gui.GetMenu(fr[0])
            win32gui.SendMessage(fr[0], ci.WM_INITMENU, hmenu, 0)
            item = find_menu_item(ci.enumerate_menu(hmenu, frame_hwnd=fr[0]), command_id)
        except Exception as e:  # noqa: BLE001
            self.log("The menu state of %d: %s" % (command_id, e))
            return None
        return None if item is None else bool(item.get("checked"))

    def break_lock(self, on: bool) -> bool:
        """Turn Edit > Break Lock Mode (menu 148, a toggle) on or off; True when it posted the
        command. Its tick is read before and after; when it cannot be read, the command is
        posted (a fresh builder starts with the mode off)."""
        state = self.menu_checked(MENU_BREAK_LOCK)
        if state is not None and state == on:
            return False
        if state is None:
            self.log("Break Lock Mode: its tick cannot be read; the command is posted.")
        self.menu_command(MENU_BREAK_LOCK)
        self.wait(0.5)
        after = self.menu_checked(MENU_BREAK_LOCK)
        if after is not None and after != on:
            raise UnitFailed("Break Lock Mode did not turn %s" % ("on" if on else "off"))
        return True

    def select_template(self, an: int) -> str:
        """Select a page's template (Break Lock Mode on): PageSelectObject of its root's AN,
        else Edit > Select All (menu 133). Returns which one did; UnitFailed when nothing is
        selected after both."""
        try:
            self.com.PageSelectObject(an)
            if int(self.com.AttributeAN()) == an:
                return "PageSelectObject(%d)" % an
        except Exception as e:  # noqa: BLE001
            self.log("PageSelectObject(%d) of the template: %s" % (an, e))
        self.menu_command(MENU_SELECT_ALL)
        self.wait(1.0)
        try:
            got = int(self.com.AttributeAN())
        except Exception as e:  # noqa: BLE001
            raise UnitFailed(
                "the template could not be selected (AN %d, then Select All): %s" % (an, e)
            ) from e
        if got <= 0:  # (no object has AN 0: nothing selected)
            raise UnitFailed("the template could not be selected (AN %d, then Select All: AN %d)" % (an, got))
        return "Select All (menu %d), AN %d first" % (MENU_SELECT_ALL, got)

    @contextlib.contextmanager
    def answer(self, prefer: str):
        """Answer the message boxes of a block with ``prefer`` (a unit that asks 'Yes')."""
        old = self.watch.prefer if self.watch is not None else None
        if self.watch is not None:
            self.watch.prefer = prefer
        try:
            yield
        finally:
            if self.watch is not None:
                self.watch.prefer = old

    @contextlib.contextmanager
    def ok_dialogs(self):
        """Answer the dialogs (not message boxes) that open during a block with their OK
        (a New Page from a template with a parameter of its own may ask for its value);
        the dialogs open before are left alone. Yields the list of those answered."""
        got: List[dict] = []
        w = self.watch
        if w is None:
            yield got
            return
        from ctgedit.backend import winui

        known = set()
        with contextlib.suppress(Exception):
            for pid in winui.pids_of("CtDraw32.exe"):
                known.update(winui.windows_of(pid))
        start = len(w.answered)
        w.ok_known = known
        w.press_ok = True
        try:
            yield got
        finally:
            w.press_ok = False
            got.extend(w.answered[start:])

    def act(self, action: tuple) -> None:
        kind = action[0]
        if kind == "rect":
            self.com.DrawRectangle(*action[1:])
        elif kind == "touch":
            self.com.PropertiesInputTouchPut(*action[1:])
        elif kind == "key":
            self.com.PropertiesInputKeyboardPutEx(*action[1:])
        else:
            raise UnitFailed("unknown action %r" % (action,))


def client_box(
    window: Sequence[int], origin: Sequence[int], size: Sequence[int]
) -> Tuple[int, int, int, int]:
    """A window's client area in a grab of its whole window rect: (left, top, right,
    bottom) in the grab's pixels. ``window`` = GetWindowRect, ``origin`` = ClientToScreen of
    the client's (0, 0), ``size`` = GetClientRect's width and height."""
    x, y = origin[0] - window[0], origin[1] - window[1]
    return x, y, x + size[0], y + size[1]


def find_menu_item(items: Sequence[dict], command_id: int) -> Optional[dict]:
    """The item with ``command_id`` in a menu tree (ctdraw_inspect.enumerate_menu: dicts
    with 'id' and their submenu's 'items'), depth first, or None."""
    for it in items:
        if it.get("id") == command_id and not it.get("has_submenu"):
            return it
        hit = find_menu_item(it.get("items") or (), command_id)
        if hit is not None:
            return hit
    return None


def ctdraw_version() -> str:
    """CtDraw32.exe's file version (its Bin folder through infra/install.py)."""
    try:
        import win32api
        from ctgedit.infra import install

        exe = os.path.join(install.bin_dir() or "", "CtDraw32.exe")
        info = win32api.GetFileVersionInfo(exe, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------------------- unit bodies
def _run_page(ctx: Oracle, unit: Unit) -> None:
    proj, page = unit.data["project"], unit.data["page"]
    ctx.open_page(proj, page)
    png = None
    if unit.data.get("shot") or ctx.shots == "all":
        png = ctx.shot()
        if png:
            ctx.emit(unit, "canvas.png", png, "CtDraw's canvas of %s after its load." % page, unit.sources[0])
    ctx.save()
    ctx.com.PageClose()
    ctx.collect_page(unit, proj, page, unit.sources[0])
    # (a listed shot that failed: its golden tests would skip as 'not captured' for good)
    if unit.data.get("shot") and ctx.shots != "none" and not png:
        raise UnitFailed("no canvas.png: the shot of %s failed (the log says why)" % page)


def _run_library(ctx: Oracle, unit: Unit) -> None:
    from ctgedit.format.element import LibraryFile

    proj, lib = unit.data["project"], unit.data["library"]
    src = unit.sources[0]
    stem = os.path.splitext(lib)[0].lower()
    start = LibraryFile(ctx.read(proj, lib) or b"")
    done, types, fresh, no_shot = [], {}, [], []
    for el in unit.data["elements"]:
        if ctx.stop.requested:
            break
        # an element that names one this builder has opened (n1 nests n0), or one of the
        # same name (FxOther's fxsym after FxLib's), is composed from CtDraw's memory
        slot = start.find(el)
        hit = linked_elements(start.block_data(slot) if slot is not None else b"", ctx.opened)
        if (stem, el.lower()) in ctx.opened:
            hit.append("%s.%s itself" % (stem, el.lower()))
        if hit and ctx.fresh_builder(
            "%s.%s names %s, which this builder has opened" % (stem, el, ", ".join(hit))
        ):
            fresh.append(el)
        try:
            types[el] = ctx.open_element(proj, lib, el)
        except UnitFailed as e:
            ctx.log("  %s" % e)
            continue
        if (lib.lower(), el) in ELEMENT_SHOTS or ctx.shots == "all":
            png = ctx.shot()
            if png:
                ctx.emit(unit, "%s.png" % safe_name(el), png, "CtDraw's element window of %s." % el, src)
            elif (lib.lower(), el) in ELEMENT_SHOTS and ctx.shots != "none":
                no_shot.append(el)
        ctx.save()
        ctx.com.PageClose()
        data = ctx.read(proj, lib)
        lf = LibraryFile(data)
        slot = lf.find(el)
        if slot is None:
            raise UnitFailed("%s is not in %s after its save" % (el, lib))
        ctx.emit(
            unit,
            "%s.block" % safe_name(el),
            lf.block_data(slot),
            "The block of %s in %s after CtDraw's save of it." % (el, lib),
            src,
        )
        done.append(el)
    data = ctx.read(proj, lib)
    ctx.emit(
        unit,
        "library" + os.path.splitext(lib)[1],
        data,
        "%s after CtDraw saved its elements in order: %s." % (lib, ", ".join(done)),
        src,
    )
    ctx.emit_json(
        unit,
        "elements.json",
        {
            "saved": done,
            "page_types": types,
            "missed": [e for e in unit.data["elements"] if e not in done],
            "fresh_builder_before": fresh,
        },
        "The elements saved, in order, the PageOpenEx page type of each and the "
        "elements opened in a fresh Graphics Builder.",
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})
    missed = [e for e in unit.data["elements"] if e not in done]
    if missed:
        raise UnitFailed("not saved: %s" % ", ".join(missed))
    if no_shot:
        raise UnitFailed("no PNG: the shot of %s failed (the log says why)" % ", ".join(no_shot))


def _run_append(ctx: Oracle, unit: Unit) -> None:
    from ctgedit.format.element import LibraryFile

    proj, lib, el, new = (unit.data[k] for k in ("project", "library", "element", "as"))
    src = unit.sources[0]
    ptype = ctx.open_element(proj, lib, el)
    ctx.com.PageSaveAsEx(proj, os.path.splitext(lib)[0], new)
    ctx.wait(1.0)
    ctx.com.PageClose()
    data = ctx.read(proj, lib)
    if data is None:
        raise UnitFailed("%s is gone after the append" % lib)
    lf = LibraryFile(data)
    slot = lf.find(new)
    if slot is None:
        raise UnitFailed("PageSaveAsEx added no %s to %s" % (new, lib))
    ctx.emit(
        unit,
        "library" + os.path.splitext(lib)[1],
        data,
        "%s after CtDraw saved %s as the new element %s." % (lib, el, new),
        src,
    )
    ctx.emit(
        unit,
        "%s.block" % safe_name(new),
        lf.block_data(slot),
        "The block of the appended element %s." % new,
        src,
    )
    ctx.emit_json(
        unit,
        "actions.json",
        {
            "PageOpenEx": [proj, os.path.splitext(lib)[0], el, ptype],
            "PageSaveAsEx": [proj, os.path.splitext(lib)[0], new],
            "elements": lf.element_names(),
        },
        "The COM calls (the page type as found) and the elements after the append.",
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


def _run_copy_paste(ctx: Oracle, unit: Unit) -> None:
    proj, page = unit.data["project"], unit.data["page"]
    ctx.open_page(proj, page)
    done = []
    for names in unit.data["copies"]:
        ans = ctx.select(unit, names)
        ctx.com.ClipboardCopy()
        ctx.com.ClipboardPaste()
        ctx.wait(1.0)
        done.append({"names": names, "an": ans})
    ctx.save()
    ctx.com.PageClose()
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": page, "copies": done},
        "The selected names, their ANs, each ClipboardCopy + ClipboardPaste.",
    )
    ctx.collect_page(unit, proj, page, unit.sources[0])


def _run_cut_link(ctx: Oracle, unit: Unit) -> None:
    """Cut Link of the page's template."""
    proj, page = unit.data["project"], unit.data["page"]
    tpl = (ctx.prepared.get("templates") or {}).get("%s/%s" % (proj, page))
    if not tpl:
        raise UnitFailed("prepare found no template on %s" % page)
    source = ctx.read(proj, page + ".ctg")
    ctx.open_page(proj, page)
    turned_on = ctx.break_lock(True)
    try:
        selected = ctx.select_template(tpl["an"])
        with ctx.answer("Yes"):
            ctx.menu_command(MENU_CUT_LINK)
            ctx.wait(2.0)
            ctx.save()
    finally:
        if turned_on:
            with contextlib.suppress(Exception):
                ctx.break_lock(False)
    ctx.com.PageClose()
    data = ctx.read(proj, page + ".ctg") or b""
    low = data.lower()
    left = tpl["reference"].lower().encode("cp1252") in low
    lost = [n for n in tpl["keep"] if n.lower().encode("cp1252") not in low]
    unchanged = data == source
    ctx.emit_json(
        unit,
        "actions.json",
        {
            "page": page,
            "template": tpl,
            "break_lock_posted": turned_on,
            "selected": selected,
            "menu_command": MENU_CUT_LINK,
            "template_reference_left": left,
            "links_lost": lost,
            "unchanged": unchanged,
        },
        "Break Lock Mode, the selection, the menu command posted; whether the saved "
        "page still names the template, the links it lost, whether it is the page "
        "CtDraw opened.",
    )
    ctx.collect_page(unit, proj, page, unit.sources[0])
    why = []
    if unchanged:
        why.append("CtDraw saved the page as it opened it")
    if left:
        why.append("the saved page still names %s" % tpl["reference"])
    if lost:
        why.append("the saved page lost the links %s (more than the template was cut)" % ", ".join(lost))
    if why:
        raise UnitFailed("Cut Link: %s" % "; ".join(why))
    # CtDraw's own PageOpen + PageSave of the cut page: its Cut Link output is not a fixed
    # point of its next load, and ctgedit writes that fixed point;
    # in a fresh Graphics Builder, as a later session opens it (this one holds the genies
    # the page links in the form it composed them from, and its template cut)
    ctx.fresh_builder("the reopen of the page Cut Link saved")
    ctx.open_page(proj, page)
    ctx.save()
    ctx.com.PageClose()
    again = ctx.read(proj, page + ".ctg")
    if again is None:
        raise UnitFailed("%s.ctg is gone after the second save" % page)
    ctx.emit(
        unit,
        "reopened.ctg",
        again,
        "%s.ctg after CtDraw's PageOpen + PageSave of the "
        "page its Cut Link saved (the same bytes when that page was a fixed point)." % page,
        unit.sources[0],
    )


def _run_turn(ctx: Oracle, unit: Unit) -> None:
    proj, page = unit.data["project"], unit.data["page"]
    ctx.open_page(proj, page)
    sel = list(unit.data["names"])
    ans = {"selection": ctx.select(unit, sel)}
    for op in unit.data["ops"]:
        if op == "R":
            ctx.com.PositionRotate()
        elif op == "H":
            ctx.com.PositionMirrorHorizontal()
        elif op == "V":
            ctx.com.PositionMirrorVertical()
        elif op == "S":
            ctx.save()
            ctx.select(unit, sel)
        elif op == "O":
            ctx.save()
            ctx.open_page(proj, page)
            ctx.select(unit, sel)
        elif op[0] == "at":
            ctx.com.PositionAt(op[1], op[2])
        elif op[0] == "select":
            sel = list(op[1])
            ctx.select(unit, sel)
        else:
            raise UnitFailed("unknown operation %r" % (op,))
    ctx.save()
    ctx.com.PageClose()
    ctx.emit_json(
        unit,
        "sequence.json",
        {"page": "%s/%s" % (proj, page), "names": unit.data["names"], "ops": unit.data["ops"], "an": ans},
        "The sequence CtDraw ran (the selected names, their ANs, the operations).",
    )
    ctx.collect_page(unit, proj, page, unit.sources[0])


def _run_input_page(ctx: Oracle, unit: Unit) -> None:
    proj, page = unit.data["project"], unit.data["page"]
    src = "inputs/%s.ctg" % page
    for gone in unit.data.get("drop_pages", ()):
        # (ctgedit's delete = the Open dialog's Delete, Yes: the files, the pages.DBF row and
        # every pg* / activex row; the baseline reset puts them back after the unit)
        from ctgedit.io import project as _project

        rep = _project.delete_page(os.path.join(ctx.inst.user, proj), gone, files=True)
        if rep.get("errors") or not rep.get("records"):
            raise UnitFailed("could not delete %s/%s first: %r" % (proj, gone, rep))
    data = ctx.put_input(proj, page)
    ctx.emit(
        unit,
        "input.ctg",
        data,
        "The page CtDraw opened: %s" % ctx.prepared["inputs"].get(page, {}).get("what", page),
        src,
    )
    ctx.open_page(proj, page)
    done = []
    for action in unit.data["actions"]:
        if action[0] == "place":
            _k, lib_project, lib, element, ext, x, y, props = action
            t = ctx.place((proj, page), lib_project, lib, element, ext, x, y, props)
            done.append(list(action) + ["type %d" % t])
        elif action[0] == "name":
            done.append(list(action) + ["AN %d" % _name_placed(ctx, proj, page, action[1])])
        else:
            ctx.act(action)
            done.append(list(action))
    ctx.save()
    ctx.com.PageClose()
    ctx.emit_json(
        unit, "actions.json", {"page": page, "actions": done}, "The COM calls of the unit, in order."
    )
    ctx.collect_page(unit, proj, page, src)


def _name_placed(ctx: Oracle, proj: str, page: str, name: str) -> int:
    """Name the one genie a place unit put on its blank page (PropertiesAttributeNamePutEx
    by its AN), so CtDraw's placement carries the name the fixture gives its own copy (the
    root's '__AN_NAME__' in its level-0 bags). Saved and checked; returns the AN."""
    from ctgedit.format import genie_place as GP, geometry as G
    from ctgedit.format.page import parse_ctg_bytes

    def genies():
        objs = parse_ctg_bytes(ctx.read(proj, page + ".ctg") or b"").page.objects
        k, out = 0, []
        while k < len(objs):
            if objs[k].class_flag and objs[k].subtype_size & 0x200:
                out.append(objs[k])
            k = GP.subtree_end(objs, k)
        return out

    found = genies()
    if len(found) != 1:
        raise UnitFailed("%s holds %d placed genies, not one" % (page, len(found)))
    an = G.live_an(found[0])
    try:
        ctx.com.PropertiesAttributeNamePutEx(an, name)
    except Exception as e:  # noqa: BLE001
        raise UnitFailed("PropertiesAttributeNamePutEx(%d, %s): %s" % (an, name, e))
    ctx.save()
    got = [G.parse_geom_header(po.body_raw).name for po in genies()]
    if got != [name]:
        raise UnitFailed("PropertiesAttributeNamePutEx(%d, %s) left the genie named %r" % (an, name, got))
    return an


def _run_named_actions(ctx: Oracle, unit: Unit) -> None:
    proj, page = unit.data["project"], unit.data["page"]
    ctx.open_page(proj, page)
    ans = ctx.select(unit, unit.data["names"])
    for action in unit.data["actions"]:
        ctx.act(action)
    ctx.save()
    ctx.com.PageClose()
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": page, "names": unit.data["names"], "an": ans, "actions": unit.data["actions"]},
        "The selected names, their ANs and the COM calls.",
    )
    ctx.collect_page(unit, proj, page, unit.sources[0])


def _run_new_page(ctx: Oracle, unit: Unit) -> None:
    linked = unit.data["linked"]
    page = "FxOracleNew%s" % ("Linked" if linked else "Unlinked")
    ctx.project("FxMain")
    want = b"normal@xga"
    order = list(range(16))
    if "resolution" in ctx.cache:
        order.remove(ctx.cache["resolution"])
        order.insert(0, ctx.cache["resolution"])
    elif not linked:
        raise UnitFailed("run the linked unit first: it finds the resolution code")
    ctx.used = True
    for res in order:
        with contextlib.suppress(Exception):
            ctx.com.PageClose()
        try:
            ctx.com.PageNew("FxMain", "fx_style", "normal", res, True, linked)
            ctx.com.PageSaveAs("FxMain", page)
        except Exception:  # noqa: BLE001
            continue
        data = ctx.read("FxMain", page + ".ctg") or b""
        if not linked or want in data.lower():
            ctx.cache["resolution"] = res
            break
        p = _find_file(os.path.join(ctx.inst.user, "FxMain"), page + ".ctg")
        with contextlib.suppress(Exception):
            ctx.com.PageClose()
        if p:
            os.remove(p)
    else:
        raise UnitFailed("PageNew made no page on normal@XGA")
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    ctx.emit_json(
        unit,
        "actions.json",
        {
            "PageNew": ["FxMain", "fx_style", "normal", ctx.cache["resolution"], True, linked],
            "PageSaveAs": ["FxMain", page],
        },
        "The COM calls (the resolution code as found).",
    )
    ctx.collect_page(unit, "FxMain", page, None)


def _run_compile(ctx: Oracle, unit: Unit) -> None:
    from dataclasses import asdict
    from ctgedit.backend import compiler

    proj = unit.data["project"]
    r = compiler.compile_project(proj, timeout=1800.0)
    ctx.emit_json(unit, "verdict.json", asdict(r), "The compiler's result on %s." % proj)


# ---------------------------------------------------------------------------- the chain step
def _put_file(ctx: Oracle, project: str, name: str, data: bytes) -> None:
    """Write a file of an Fx folder back (its own spelling kept): a stage of a unit."""
    folder = os.path.join(ctx.inst.user, project)
    _write(_find_file(folder, name) or os.path.join(folder, name), data)


def _emit_stage(
    ctx: Oracle,
    unit: Unit,
    project: str,
    lib: str,
    stage: str,
    elements: Sequence[str],
    what: str,
    library: bool = True,
    source: Optional[str] = None,
) -> bytes:
    """``<stage>/library.<ext>`` (unless not ``library``) and ``<stage>/<element>.block``
    of each of ``elements`` as CtDraw left them; returns the library's bytes. ``source``:
    the fixture file they come from (the unit's first source by default)."""
    from ctgedit.format.element import LibraryFile

    data = ctx.read(project, lib)
    if data is None:
        raise UnitFailed("%s is gone after %s" % (lib, what))
    src = source or (unit.sources[0] if unit.sources else None)
    if library:
        ctx.emit(
            unit,
            "%s/library%s" % (stage, os.path.splitext(lib)[1].lower()),
            data,
            "%s after %s." % (lib, what),
            src,
        )
    lf = LibraryFile(data)
    for el in elements:
        slot = lf.find(el)
        if slot is None:
            raise UnitFailed("%s is not in %s after %s" % (el, lib, what))
        ctx.emit(
            unit,
            "%s/%s.block" % (stage, safe_name(el)),
            lf.block_data(slot),
            "The block of %s in %s after %s." % (el, lib, what),
            src,
        )
    return data


def _texts(ctx: Oracle) -> List[int]:
    """The ANs of the texts of the active window, in z-order (PageSelectFirstObject /
    PageSelectNextObject until the error that ends them; AttributeClass, never
    AttributeText*)."""
    out: List[int] = []
    try:
        ctx.com.PageSelectFirstObject()
    except Exception:  # noqa: BLE001 (no object)
        return out
    for _n in range(500):
        try:
            if str(ctx.com.AttributeClass()) == "Text":
                out.append(int(ctx.com.AttributeAN()))
        except Exception:  # noqa: BLE001
            break
        try:
            ctx.com.PageSelectNextObject()
        except Exception:  # noqa: BLE001 (the last one)
            break
    return out


def _select_text(ctx: Oracle, key: str) -> int:
    """Select the one text of the active element window: the AN prepare mapped (``key`` =
    'project/library/element') when CtDraw shows a text there, else the only text the
    window holds. Returns its AN."""
    info = (ctx.prepared.get("elements") or {}).get(key) or {}
    for rec in info.get("records", ()):
        if rec.get("type_code") != 0x05:
            continue
        with contextlib.suppress(Exception):
            ctx.com.PageSelectObject(rec["an"])
            if int(ctx.com.AttributeAN()) == rec["an"] and str(ctx.com.AttributeClass()) == "Text":
                return rec["an"]
    texts = _texts(ctx)
    if len(texts) != 1:
        raise UnitFailed("%s shows %d texts (%s), not one" % (key, len(texts), texts))
    ctx.com.PageSelectObject(texts[0])
    if int(ctx.com.AttributeAN()) != texts[0]:
        raise UnitFailed("the text AN %d of %s could not be selected" % (texts[0], key))
    return texts[0]


def _follow(
    ctx: Oracle, unit: Unit, project: str, lib: str, elements: Sequence[str], stage: str, calls: list
) -> None:
    """CtDraw's open + save of each of ``elements`` in order (they nest the edited one):
    ``<stage>/<element>/`` gets the library and the element's block after its save."""
    for el in elements:
        ptype = ctx.open_element(project, lib, el)
        ctx.save()
        ctx.com.PageClose()
        calls.append({"open + save": el, "page type": ptype, "stage": "%s/%s" % (stage, el)})
        _emit_stage(
            ctx,
            unit,
            project,
            lib,
            "%s/%s" % (stage, el),
            [el],
            "CtDraw's open + save of %s (%s)" % (el, stage),
        )


def _run_chain_gained(ctx: Oracle, unit: Unit) -> None:
    """Q1: a member gained among kept ones in a nested
    genie. n0 gains a text in CtDraw and is saved; then n1 .. n4 are opened and saved in
    order (each composes its copy of the one it nests), in the same Graphics Builder and,
    from n0's save, in a fresh one (a later session)."""
    proj, lib = unit.data["project"], unit.data["library"]
    text, x, y = CHAIN_GAINED
    calls: list = []
    ctx.open_element(proj, lib, CHAIN_EDITED)
    before = _texts(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    after = _texts(ctx)
    gained = [a for a in after if a not in before]
    if len(gained) != 1:
        raise UnitFailed("DrawText left the texts %s of %s (before: %s)" % (after, CHAIN_EDITED, before))
    ctx.save()
    ctx.com.PageClose()
    calls.append(
        {
            "open": CHAIN_EDITED,
            "DrawText": [text, x, y],
            "texts before": before,
            "texts after": after,
            "stage": "n0_gained",
        }
    )
    saved = _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "n0_gained",
        [CHAIN_EDITED],
        "CtDraw's DrawText of %r in %s and its save" % (text, CHAIN_EDITED),
    )
    _follow(ctx, unit, proj, lib, CHAIN_FOLLOW, "same", calls)
    _put_file(ctx, proj, lib, saved)
    ctx.fresh_builder(
        "the load + save of %s from %s's save, as a later session" % (", ".join(CHAIN_FOLLOW), CHAIN_EDITED)
    )
    calls.append({"fresh Graphics Builder": True, "library": "as after n0_gained"})
    _follow(ctx, unit, proj, lib, CHAIN_FOLLOW, "fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


def _run_chain_mirror(ctx: Oracle, unit: Unit) -> None:
    """Q4: a text mirrored back in a genie's definition.
    n0's text is mirrored and n0 saved, n1 opened and saved; then the text is mirrored
    back, n0 saved, n1 opened and saved (in the same Graphics Builder, and again from n0's
    second save in a fresh one)."""
    proj, lib = unit.data["project"], unit.data["library"]
    key = "%s/%s/%s" % (proj, lib, CHAIN_EDITED)
    calls: list = []

    def mirror(stage: str, what: str) -> bytes:
        ctx.open_element(proj, lib, CHAIN_EDITED)
        an = _select_text(ctx, key)
        ctx.com.PositionMirrorHorizontal()
        ctx.save()
        ctx.com.PageClose()
        calls.append(
            {"open": CHAIN_EDITED, "selected text AN": an, "PositionMirrorHorizontal": True, "stage": stage}
        )
        return _emit_stage(ctx, unit, proj, lib, stage, [CHAIN_EDITED], what)

    mirror("n0_mirrored", "n0's text mirrored (PositionMirrorHorizontal) and n0 saved")
    _follow(ctx, unit, proj, lib, ("n1",), "after_mirror", calls)
    back = mirror("n0_back", "n0's text mirrored back (a second PositionMirrorHorizontal) and n0 saved")
    _follow(ctx, unit, proj, lib, ("n1",), "after_back", calls)
    _put_file(ctx, proj, lib, back)
    ctx.fresh_builder("the load + save of n1 after n0's text was mirrored back, as a later session")
    calls.append({"fresh Graphics Builder": True, "library": "as after n0_back"})
    _follow(ctx, unit, proj, lib, ("n1",), "after_back_fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


def _template_name(base: str, res_name: str, title: bool) -> str:
    return "%s@%s%s" % (base, "" if title else "-", res_name)


def _page_new(ctx: Oracle, args: tuple, page: str) -> Tuple[Optional[bytes], str]:
    """One PageNew(*args) + PageSaveAs into FxMain as ``page``: (the .ctg or None, what
    happened)."""
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    try:
        ctx.com.PageNew(*args)
    except Exception as e:  # noqa: BLE001
        return None, "PageNew: %s" % (e,)
    ctx.wait(1.0)
    try:
        ctx.com.PageSaveAs("FxMain", page)
    except Exception as e:  # noqa: BLE001
        with contextlib.suppress(Exception):
            ctx.com.PageClose()
        return None, "PageSaveAs: %s" % (e,)
    ctx.wait(1.0)
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    data = ctx.read("FxMain", page + ".ctg")
    return data, "saved" if data is not None else "no %s.ctg" % page


def _new_page_from(
    ctx: Oracle,
    unit: Unit,
    style: str,
    template: str,
    res: int,
    title: bool,
    page: str,
    stage: str,
    calls: list,
    want: Optional[bytes] = None,
    control: Optional[str] = None,
) -> Optional[bytes]:
    """CtDraw's New Page from a template."""
    ctx.project("FxMain")
    ctx.used = True
    tries = [
        ("FxMain", style, template, res, title, True),
        (OUTER_PROJECT, style, template, res, title, True),
        ("FxMain", style, template, 0, title, True),
    ]
    attempts = []
    data = None
    for args in tries:
        data, what = _page_new(ctx, args, page)
        attempts.append({"PageNew": list(args), "PageSaveAs": ["FxMain", page], "result": what})
        if data is not None:
            break
    calls.append({"New Page": attempts, "stage": stage})
    if data is None:
        if control is None:
            raise UnitFailed("PageNew from %s %s: %s" % (style, template, attempts))
        got, what = _page_new(ctx, ("FxMain", style, control, res, title, True), page + "Control")
        attempts.append({"control PageNew": ["FxMain", style, control, res, title, True], "result": what})
        if got is None:
            raise UnitFailed("PageNew refuses even the control %s %s: %s" % (style, control, attempts))
        ctx.emit_json(
            unit,
            "%s/attempts.json" % stage,
            {"attempts": attempts},
            "CtDraw's PageNew refused %s %s; the control %s made a page." % (style, template, control),
        )
        return None
    if want is not None and want.lower() not in data.lower():
        raise UnitFailed("the page %s does not name %r" % (page, want))
    ctx.emit(
        unit,
        "%s/page.ctg" % stage,
        data,
        "%s.ctg: CtDraw's New Page from %s %s (%s)." % (page, style, template, stage),
    )
    ctf = ctx.read("FxMain", page + ".ctF")
    if ctf is not None:
        ctx.emit(unit, "%s/page.ctF" % stage, ctf, "The .ctF CtDraw wrote with it.")
    return data


def _run_outer_template(ctx: Oracle, unit: Unit) -> None:
    """A template that holds another template's copy."""
    from ctgedit.format.element import LibraryFile

    proj, style = unit.data["project"], OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    inner = _template_name(base, "XGA", title)
    calls: list = []
    ctx.project(proj)
    ctx.used = True
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    names0 = set(LibraryFile(ctx.read(proj, lib) or b"").element_names())
    ctx.com.PageNewTemplate(proj, style, base, res, title, True)
    ctx.wait(1.0)
    calls.append({"PageNewTemplate": [proj, style, base, res, title, True], "PageName": ctx.page_name()})
    made = None
    for name in (OUTER_NAME, _template_name(OUTER_NAME, "XGA", title)):
        try:
            ctx.com.PageSaveAsEx(proj, style, name)
        except Exception as e:  # noqa: BLE001
            calls.append({"PageSaveAsEx": [proj, style, name], "error": str(e)})
            continue
        ctx.wait(1.0)
        new = [n for n in LibraryFile(ctx.read(proj, lib) or b"").element_names() if n not in names0]
        calls.append({"PageSaveAsEx": [proj, style, name], "new elements": new})
        if new:
            made = new[0]
            break
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    if made is None:
        raise UnitFailed("PageNewTemplate + PageSaveAsEx made no template in %s" % lib)
    ctx.opened.add((style.lower(), made.lower()))
    lf = LibraryFile(ctx.read(proj, lib) or b"")
    link = ("%s.%s.<ctt" % (style, inner)).encode("cp1252")
    block = lf.block_data(lf.find(made)).lower()
    if link.lower() not in block:
        _emit_stage(ctx, unit, proj, lib, "made", [made], "PageNewTemplate + PageSaveAsEx")
        raise UnitFailed("the template %s CtDraw made does not name %r" % (made, link))
    if b"_library" in block:
        # (the base template's nested genie can become the placeholder DummyMarker of a
        # library that did not open, its entry a zero guid)
        _emit_stage(ctx, unit, proj, lib, "made", [made], "PageNewTemplate + PageSaveAsEx")
        raise UnitFailed(
            "the template %s CtDraw made holds a placeholder (a library it "
            "nests did not open): capture the unit again" % made
        )
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "made",
        [made, inner],
        "PageNewTemplate from %s, linked, and PageSaveAsEx as %s" % (inner, made),
    )
    outer_base = made.split("@")[0]
    # Q3: its load + save, unedited, in a later session
    ctx.fresh_builder("the load + save of the template CtDraw made, as a later session")
    _follow(ctx, unit, proj, lib, (made,), "plain", calls)
    # Q2: the inner template changes, then the outer's load + save and a New Page from it
    ctx.open_element(proj, lib, inner)
    ctx.com.DrawRectangle(*INNER_RECT)
    ctx.wait(0.5)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": inner, "DrawRectangle": list(INNER_RECT), "stage": "inner_changed"})
    changed = _emit_stage(
        ctx, unit, proj, lib, "inner_changed", [inner, made], "a rectangle drawn on %s and its save" % inner
    )
    _follow(ctx, unit, proj, lib, (made,), "same", calls)
    _new_page_from(
        ctx,
        unit,
        style,
        outer_base,
        res,
        title,
        "FxOracleOuterNew",
        "same/new_page",
        calls,
        want=("%s.%s.<ctt" % (style, made)).encode(),
        control=base,
    )
    # again from the inner's save, in a later session: the New Page first, then the outer
    _put_file(ctx, proj, lib, changed)
    ctx.fresh_builder(
        "a New Page from %s and its load + save after %s changed, as a later session" % (made, inner)
    )
    calls.append({"fresh Graphics Builder": True, "library": "as after inner_changed"})
    _new_page_from(
        ctx,
        unit,
        style,
        outer_base,
        res,
        title,
        "FxOracleOuterNew2",
        "fresh/new_page",
        calls,
        want=("%s.%s.<ctt" % (style, made)).encode(),
        control=base,
    )
    _follow(ctx, unit, proj, lib, (made,), "fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "made": made, "inner": inner, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(
        unit,
        skip={"%s/%s" % (proj, lib)}
        | {
            "FxMain/%s.%s" % (p, e) for p in ("FxOracleOuterNew", "FxOracleOuterNew2") for e in ("ctg", "ctf")
        },
    )


def _run_shipped_template(ctx: Oracle, unit: Unit) -> None:
    """A shipped template that holds another one's copy."""
    if _inside(ctx.out_root, ORACLE):
        raise UnitFailed(
            "this unit captures AVEVA content: run it with --out outside tests\\fixtures\\oracle"
        )
    proj, lib = unit.data["project"], SHIPPED_LIB
    style = os.path.splitext(lib)[0]
    base, res, title = SHIPPED_OUTER
    outer = _template_name(base, "XGA", title)
    real = _find_file(os.path.join(ctx.inst.user, "Include"), lib)
    if real is None:
        raise UnitFailed("the install's Include has no %s" % lib)
    real_sha = file_sha1(real)
    keep = os.path.join(ctx.change.run_dir, "include_" + lib)
    shutil.copyfile(real, keep)
    shutil.copyfile(real, os.path.join(ctx.inst.user, proj, lib))
    calls: list = [{"copied": "Include/%s -> %s/%s" % (os.path.basename(real), proj, lib), "sha1": real_sha}]
    try:
        _emit_stage(
            ctx,
            unit,
            proj,
            lib,
            "source",
            [outer, SHIPPED_INNER],
            "the copy from the install's Include",
            library=False,
        )
        ctx.open_element(proj, lib, outer)
        ctx.save()
        ctx.com.PageClose()
        calls.append({"open + save": outer, "stage": "plain"})
        _emit_stage(
            ctx,
            unit,
            proj,
            lib,
            "plain",
            [outer, SHIPPED_INNER],
            "CtDraw's open + save of %s, unedited" % outer,
            library=False,
        )
        ctx.open_element(proj, lib, SHIPPED_INNER)
        ctx.com.DrawRectangle(*INNER_RECT)
        ctx.wait(0.5)
        ctx.save()
        ctx.com.PageClose()
        calls.append({"open": SHIPPED_INNER, "DrawRectangle": list(INNER_RECT), "stage": "inner_changed"})
        _emit_stage(
            ctx,
            unit,
            proj,
            lib,
            "inner_changed",
            [SHIPPED_INNER, outer],
            "a rectangle drawn on %s and its save" % SHIPPED_INNER,
            library=False,
        )
        ctx.open_element(proj, lib, outer)
        ctx.save()
        ctx.com.PageClose()
        calls.append({"open + save": outer, "stage": "same"})
        _emit_stage(
            ctx,
            unit,
            proj,
            lib,
            "same",
            [outer],
            "CtDraw's open + save of %s after %s changed" % (outer, SHIPPED_INNER),
            library=False,
        )
        _new_page_from(
            ctx,
            unit,
            style,
            base,
            res,
            title,
            "FxOracleShipped",
            "same/new_page",
            calls,
            want=("%s.%s.<ctt" % (style, outer)).encode(),
            control=SHIPPED_INNER.split("@")[0],
        )
    finally:
        now = file_sha1(real)
        if now != real_sha:
            shutil.copyfile(keep, real)
            ctx.log(
                "The install's Include %s changed during the unit: put back (%s)."
                % (lib, "verified" if file_sha1(real) == real_sha else "NOT VERIFIED")
            )
        calls.append({"Include file unchanged": now == real_sha})
        with contextlib.suppress(OSError):
            os.remove(keep)
        ctx.emit_json(
            unit,
            "actions.json",
            {"library": "%s/%s" % (proj, lib), "outer": outer, "inner": SHIPPED_INNER, "calls": calls},
            "The COM calls of the unit in order (the stages name their folders).",
        )
    if now != real_sha:
        raise UnitFailed("the install's Include %s changed during the unit (put back)" % lib)
    ctx.collect(
        unit, skip={"%s/%s" % (proj, lib)} | {"FxMain/FxOracleShipped.%s" % e for e in ("ctg", "ctf")}
    )


# ---------------------------------------------------------------------------- the chain4 step
def _objects(ctx: Oracle) -> List[Tuple[int, str]]:
    """(AN, class) of the objects of the active window, in z-order (PageSelectFirstObject /
    PageSelectNextObject until the error that ends them, or an AN met again; AttributeClass,
    never AttributeText*)."""
    out: List[Tuple[int, str]] = []
    try:
        ctx.com.PageSelectFirstObject()
    except Exception:  # noqa: BLE001 (no object)
        return out
    seen = set()
    for _n in range(2000):
        try:
            an = int(ctx.com.AttributeAN())
            cls = str(ctx.com.AttributeClass())
        except Exception:  # noqa: BLE001
            break
        if an in seen:
            break
        seen.add(an)
        out.append((an, cls))
        try:
            ctx.com.PageSelectNextObject()
        except Exception:  # noqa: BLE001 (the last one)
            break
    return out


def _drawn(ctx: Oracle, before: Sequence[Tuple[int, str]], cls: str, what: str) -> int:
    """The AN of the one object of class ``cls`` a Draw* call added (``before``: the
    objects before it). CtDraw's class names: 'Text', 'SquareV3' (a rectangle)."""
    old = {an for an, _c in before}
    new = [an for an, c in _objects(ctx) if an not in old and c == cls]
    if len(new) != 1:
        raise UnitFailed("%s added %d objects of the class %s (%s)" % (what, len(new), cls, new))
    return new[0]


def _select_an(ctx: Oracle, an: int) -> None:
    ctx.com.PageSelectObject(an)
    got = int(ctx.com.AttributeAN())
    if got != an:
        raise UnitFailed("PageSelectObject(%d) selected AN %d" % (an, got))


def _probe(ctx: Oracle) -> dict:
    """What CtDraw's getters say of the selected object (each getter's error kept): its
    matrix, position, extent and base coordinates."""
    out: dict = {}
    for name in (
        "AttributeTransformationMatrixGet",
        "AttributeBaseCoordinates",
        "AttributeX",
        "AttributeY",
        "AttributeXEx",
        "AttributeYEx",
        "AttributeExtentX",
        "AttributeExtentY",
    ):
        try:
            v = getattr(ctx.com, name)()
            out[name] = list(v) if isinstance(v, (tuple, list)) else v
        except Exception as e:  # noqa: BLE001
            out[name] = "error: %s" % (e,)
    return out


def _record_geometry(po) -> dict:
    from ctgedit.format import geometry as G
    from ctgedit.format.placement import read_slots

    gh = G.parse_geom_header(po.body_raw)
    out = {"type_code": po.type_code, "an": G.live_an(po)}
    if gh is not None:
        out.update(anchor=list(gh.anchor), rect1=list(gh.rect1), rect2=list(gh.rect2))
    with contextlib.suppress(Exception):
        out["slots"] = [[sl[0], [round(v, 6) for v in sl[1]]] for sl in read_slots(po.body_raw)[0]]
    return out


def _page_record(data: bytes, an: int) -> dict:
    """The geometry of the record of a saved page whose AN is ``an``."""
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg_bytes

    for po in parse_ctg_bytes(data).page.objects:
        if G.live_an(po) == an:
            return _record_geometry(po)
    raise UnitFailed("the saved page has no record with AN %d" % an)


def _element_record(data: bytes, lib: str, element: str, an: int) -> dict:
    """The geometry of the record of a library element whose AN (in its window) is ``an``."""
    from ctgedit.format import geometry as G
    from ctgedit.format.element import LibraryFile, open_element

    pe = open_element(lib, element, library=LibraryFile(data))
    for po in pe.tree:
        if G.live_an(po) == an:
            return _record_geometry(po)
    raise UnitFailed("%s has no record with AN %d" % (element, an))


def _scale_selected(ctx: Oracle, rec: dict, s: float, what: str, compose: bool = True) -> dict:
    """Scale the selected object about its box's top left."""
    l, t = rec["rect1"][0], rec["rect1"][1]
    before = _probe(ctx)
    cur = before.get("AttributeTransformationMatrixGet")
    if not compose or not isinstance(cur, list) or len(cur) != 6:
        cur = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    a, b, c, d, h, k = (float(v) for v in cur)
    m = [s * a, s * b, s * c, s * d, s * h + l - s * l, s * k + t - s * t]
    ctx.com.AttributeTransformationMatrixPut(*m)
    ctx.wait(0.5)
    return {
        "scaled": what,
        "composed onto": [a, b, c, d, h, k],
        "AttributeTransformationMatrixPut": m,
        "record before": rec,
        "getters before": before,
        "getters after": _probe(ctx),
    }


def _stage_page(
    ctx: Oracle, unit: Unit, project: str, page: str, stage: str, what: str, source: Optional[str] = None
) -> bytes:
    """``<stage>/page.ctg`` (and its .ctF) as CtDraw last saved the page; returns the .ctg."""
    data = ctx.read(project, page + ".ctg")
    if data is None:
        raise UnitFailed("%s.ctg is gone after %s" % (page, what))
    ctx.emit(unit, "%s/page.ctg" % stage, data, "%s.ctg after %s." % (page, what), source)
    ctf = ctx.read(project, page + ".ctF")
    if ctf is not None:
        ctx.emit(unit, "%s/page.ctF" % stage, ctf, "The .ctF CtDraw wrote with it.", source)
    return data


def _page_skip(project: str, *pages: str) -> set:
    return {"%s/%s.%s" % (project, p, e) for p in pages for e in ("ctg", "ctf")}


def _make_outer(ctx: Oracle, unit: Unit, calls: list) -> str:
    """PageNewTemplate from OUTER_BASE in OUTER_STYLE, linked, saved as OUTER_NAME
    (PageSaveAsEx): ``made/`` holds the library, its block and the inner's. Returns the new
    template's name (CtDraw adds the resolution)."""
    from ctgedit.format.element import LibraryFile

    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    inner = _template_name(base, "XGA", title)
    ctx.project(proj)
    ctx.used = True
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    names0 = set(LibraryFile(ctx.read(proj, lib) or b"").element_names())
    ctx.com.PageNewTemplate(proj, style, base, res, title, True)
    ctx.wait(1.0)
    calls.append({"PageNewTemplate": [proj, style, base, res, title, True], "PageName": ctx.page_name()})
    made = None
    for name in (OUTER_NAME, _template_name(OUTER_NAME, "XGA", title)):
        try:
            ctx.com.PageSaveAsEx(proj, style, name)
        except Exception as e:  # noqa: BLE001
            calls.append({"PageSaveAsEx": [proj, style, name], "error": str(e)})
            continue
        ctx.wait(1.0)
        new = [n for n in LibraryFile(ctx.read(proj, lib) or b"").element_names() if n not in names0]
        calls.append({"PageSaveAsEx": [proj, style, name], "new elements": new})
        if new:
            made = new[0]
            break
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    if made is None:
        raise UnitFailed("PageNewTemplate + PageSaveAsEx made no template in %s" % lib)
    ctx.opened.add((style.lower(), made.lower()))
    lf = LibraryFile(ctx.read(proj, lib) or b"")
    block = lf.block_data(lf.find(made)).lower()
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "made",
        [made, inner],
        "PageNewTemplate from %s, linked, and PageSaveAsEx as %s" % (inner, made),
    )
    if ("%s.%s.<ctt" % (style, inner)).lower().encode("cp1252") not in block:
        raise UnitFailed("the template %s CtDraw made does not name %s.%s" % (made, style, inner))
    if b"_library" in block:
        raise UnitFailed(
            "the template %s CtDraw made holds a placeholder (a library it "
            "nests did not open): capture the unit again" % made
        )
    return made


def _change_inner(ctx: Oracle, unit: Unit, calls: list, made: str) -> bytes:
    """INNER_RECT drawn on the inner template and saved: ``inner_changed/``."""
    proj, lib = OUTER_PROJECT, OUTER_STYLE + ".ctt"
    inner = _template_name(OUTER_BASE[0], "XGA", OUTER_BASE[2])
    ctx.open_element(proj, lib, inner)
    ctx.com.DrawRectangle(*INNER_RECT)
    ctx.wait(0.5)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": inner, "DrawRectangle": list(INNER_RECT), "stage": "inner_changed"})
    return _emit_stage(
        ctx, unit, proj, lib, "inner_changed", [inner, made], "a rectangle drawn on %s and its save" % inner
    )


def _new_page4(
    ctx: Oracle,
    unit: Unit,
    style: str,
    template: str,
    res: int,
    title: bool,
    page: str,
    stage: str,
    calls: list,
    want: bytes,
    control: Optional[str],
) -> Optional[bytes]:
    """_new_page_from with the dialogs that open answered OK (a template with a parameter
    of its own), the ones answered recorded with the call."""
    with ctx.ok_dialogs() as answered:
        data = _new_page_from(
            ctx, unit, style, template, res, title, page, stage, calls, want=want, control=control
        )
    if answered:
        calls.append({"dialogs answered OK": answered, "stage": stage})
    return data


def _run_outer_param(ctx: Oracle, unit: Unit) -> None:
    """A template holding another's copy gains a parameter."""
    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    inner = _template_name(base, "XGA", title)
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    outer_base = made.split("@")[0]
    link = ("%s.%s.<ctt" % (style, made)).encode()
    text, x, y = OUTER_PARAM
    ctx.open_element(proj, lib, made)
    before = _objects(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": made, "DrawText": [text, x, y], "AN": an, "stage": "param_added"})
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "param_added",
        [made, inner],
        "DrawText of %r on %s and its save (its first own parameter)" % (text, made),
    )
    ctx.fresh_builder("the load + save of %s with its parameter, as a later session" % made)
    _follow(ctx, unit, proj, lib, (made,), "plain", calls)
    changed = _change_inner(ctx, unit, calls, made)
    _follow(ctx, unit, proj, lib, (made,), "same", calls)
    _new_page4(
        ctx, unit, style, outer_base, res, title, "FxOracleParamNew", "same/new_page", calls, link, base
    )
    _put_file(ctx, proj, lib, changed)
    ctx.fresh_builder(
        "a New Page from %s and its load + save after %s changed, as a later session" % (made, inner)
    )
    calls.append({"fresh Graphics Builder": True, "library": "as after inner_changed"})
    _new_page4(
        ctx, unit, style, outer_base, res, title, "FxOracleParamNew2", "fresh/new_page", calls, link, base
    )
    _follow(ctx, unit, proj, lib, (made,), "fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "made": made, "inner": inner, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(
        unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", "FxOracleParamNew", "FxOracleParamNew2")
    )


def _run_shipped_doubletrend(ctx: Oracle, unit: Unit) -> None:
    """A shipped template holding another's copy."""
    if _inside(ctx.out_root, ORACLE):
        raise UnitFailed(
            "this unit captures AVEVA content: run it with --out outside tests\\fixtures\\oracle"
        )
    proj = unit.data["project"]
    lib = SHIPPED4_LIBS[0][0]
    style = os.path.splitext(lib)[0].lower()
    base, res, title = SHIPPED4_OUTER
    outer = _template_name(base, "XGA", title)
    calls: list = []
    real: Dict[str, Tuple[str, str]] = {}
    for name, targets in SHIPPED4_LIBS:
        p = _find_file(os.path.join(ctx.inst.user, "Include"), name)
        if p is None:
            raise UnitFailed("the install's Include has no %s" % name)
        real[name] = (p, file_sha1(p))
        for t in targets:
            shutil.copyfile(p, os.path.join(ctx.inst.user, t, name))
            calls.append(
                {"copied": "Include/%s -> %s/%s" % (os.path.basename(p), t, name), "sha1": real[name][1]}
            )
    source = ctx.read(proj, lib)
    link = ("%s.%s.<ctt" % (style, outer)).encode()
    try:
        _emit_stage(
            ctx,
            unit,
            proj,
            lib,
            "source",
            [outer, SHIPPED4_INNER],
            "the copy from the install's Include",
            library=False,
        )
        ctx.open_element(proj, lib, outer)
        ctx.save()
        ctx.com.PageClose()
        calls.append({"open + save": outer, "stage": "plain"})
        _emit_stage(
            ctx,
            unit,
            proj,
            lib,
            "plain",
            [outer, SHIPPED4_INNER],
            "CtDraw's open + save of %s, unedited" % outer,
            library=False,
        )
        _new_page4(
            ctx,
            unit,
            style,
            base,
            res,
            title,
            "FxOracleShipped4",
            "same/new_page",
            calls,
            link,
            SHIPPED4_INNER.split("@")[0],
        )
        _put_file(ctx, proj, lib, source)
        ctx.fresh_builder("a New Page from %s as shipped, as a later session" % outer)
        calls.append({"fresh Graphics Builder": True, "library": "as copied"})
        _new_page4(
            ctx,
            unit,
            style,
            base,
            res,
            title,
            "FxOracleShipped4b",
            "fresh/new_page",
            calls,
            link,
            SHIPPED4_INNER.split("@")[0],
        )
        ctx.open_element(proj, lib, outer)
        ctx.save()
        ctx.com.PageClose()
        calls.append({"open + save": outer, "stage": "fresh"})
        _emit_stage(
            ctx,
            unit,
            proj,
            lib,
            "fresh",
            [outer],
            "CtDraw's open + save of %s after the New Page, in a fresh Graphics Builder" % outer,
            library=False,
        )
    finally:
        changed = []
        for name, (p, sha) in real.items():
            if file_sha1(p) != sha:
                changed.append(name)
                ctx.log(
                    "The install's Include %s changed during the unit: it is NOT put "
                    "back by the tool (check it)." % name
                )
        calls.append({"Include files unchanged": not changed})
        ctx.emit_json(
            unit,
            "actions.json",
            {"library": "%s/%s" % (proj, lib), "outer": outer, "inner": SHIPPED4_INNER, "calls": calls},
            "The COM calls of the unit in order (the stages name their folders).",
        )
    if changed:
        raise UnitFailed("the install's Include %s changed during the unit" % ", ".join(changed))
    ctx.collect(
        unit,
        skip={"%s/%s" % (proj, lib)}
        | {"%s/%s" % (t, n) for n, ts in SHIPPED4_LIBS for t in ts}
        | _page_skip("FxMain", "FxOracleShipped4", "FxOracleShipped4b"),
    )


def _run_mirror_resize(ctx: Oracle, unit: Unit) -> None:
    """A mirrored text resized."""
    proj, page = unit.data["project"], unit.data["page"]
    src = "inputs/%s.ctg" % page
    data = ctx.put_input(proj, page)
    ctx.emit(
        unit,
        "input.ctg",
        data,
        "The page CtDraw opened: %s" % ctx.prepared["inputs"].get(page, {}).get("what", page),
        src,
    )
    ctx.open_page(proj, page)
    calls: list = []
    made: Dict[str, int] = {}
    for text, x, y, _m, _c in RESIZE_TEXTS:
        before = _objects(ctx)
        ctx.com.DrawText(text, x, y)
        ctx.wait(0.5)
        made[text] = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    before = _objects(ctx)
    ctx.com.DrawRectangle(*RESIZE_RECT)
    ctx.wait(0.5)
    made["rectangle"] = _drawn(ctx, before, "SquareV3", "DrawRectangle")
    ctx.save()
    calls.append(
        {
            "DrawText": [list(t[:3]) for t in RESIZE_TEXTS],
            "DrawRectangle": list(RESIZE_RECT),
            "ANs": made,
            "stage": "drawn",
        }
    )
    _stage_page(ctx, unit, proj, page, "drawn", "the texts and the rectangle drawn", src)
    for text, _x, _y, mirror, _c in RESIZE_TEXTS:
        if mirror:
            _select_an(ctx, made[text])
            ctx.com.PositionMirrorHorizontal()
            ctx.wait(0.5)
            calls.append(
                {
                    "PositionMirrorHorizontal": text,
                    "AN": made[text],
                    "getters": _probe(ctx),
                    "stage": "mirrored",
                }
            )
    ctx.save()
    saved = _stage_page(ctx, unit, proj, page, "mirrored", "the texts mirrored", src)
    composed = {t[0]: t[4] for t in RESIZE_TEXTS}
    for key, an in made.items():
        _select_an(ctx, an)
        call = _scale_selected(
            ctx, _page_record(saved, an), RESIZE_SCALE, key, compose=composed.get(key, True)
        )
        call.update(AN=an, stage="resized")
        calls.append(call)
    ctx.save()
    _stage_page(
        ctx,
        unit,
        proj,
        page,
        "resized",
        "each object scaled %g times about its box's top left" % RESIZE_SCALE,
        src,
    )
    ctx.com.PageClose()
    ctx.fresh_builder("the load + save of the resized page, as a later session")
    ctx.open_page(proj, page)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open + save": page, "fresh Graphics Builder": True, "stage": "reopened"})
    _stage_page(ctx, unit, proj, page, "reopened", "its load + save in a fresh Graphics Builder", src)
    # the same in a genie element: n0's text
    lp, lib = CHAIN_LIB
    key = "%s/%s/%s" % (lp, lib, CHAIN_EDITED)
    ctx.open_element(lp, lib, CHAIN_EDITED)
    an = _select_text(ctx, key)
    ctx.com.PositionMirrorHorizontal()
    ctx.wait(0.5)
    ctx.save()
    ctx.com.PageClose()
    calls.append(
        {
            "open": CHAIN_EDITED,
            "selected text AN": an,
            "PositionMirrorHorizontal": True,
            "stage": "n0_mirrored",
        }
    )
    mirrored = _emit_stage(
        ctx, unit, lp, lib, "n0_mirrored", [CHAIN_EDITED], "n0's text mirrored and n0 saved"
    )
    ctx.open_element(lp, lib, CHAIN_EDITED)
    an = _select_text(ctx, key)
    call = _scale_selected(ctx, _element_record(mirrored, lib, CHAIN_EDITED, an), RESIZE_SCALE, "n0's text")
    ctx.save()
    ctx.com.PageClose()
    call.update(AN=an, stage="n0_resized")
    calls.append(call)
    _emit_stage(
        ctx,
        unit,
        lp,
        lib,
        "n0_resized",
        [CHAIN_EDITED],
        "n0's mirrored text scaled %g times about its box's top left and n0 saved" % RESIZE_SCALE,
    )
    _follow(ctx, unit, lp, lib, ("n1",), "after_resize", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "%s/%s" % (proj, page), "calls": calls},
        "The COM calls of the unit in order, with what CtDraw's getters said (the "
        "stages name their folders).",
        src,
    )
    ctx.collect(unit, skip={"%s/%s" % (lp, lib)} | _page_skip(proj, page))


def _the_genie(data: bytes) -> Tuple[int, dict]:
    """(AN, geometry) of the one genie root a page holds."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg_bytes

    objs = parse_ctg_bytes(data).page.objects
    k, found = 0, []
    while k < len(objs):
        if objs[k].class_flag and objs[k].subtype_size & 0x200:
            found.append(objs[k])
        k = GP.subtree_end(objs, k)
    if len(found) != 1:
        raise UnitFailed("the page holds %d placed genies, not one" % len(found))
    rec = _record_geometry(found[0])
    return rec["an"], rec


def _run_placeholder_turned(ctx: Oracle, unit: Unit) -> None:
    """A placeholder genie with a turn and a scale."""
    proj, page = unit.data["project"], unit.data["page"]
    src = "inputs/%s.ctg" % page
    data = ctx.put_input(proj, page)
    ctx.emit(
        unit,
        "input.ctg",
        data,
        "The page CtDraw opened: %s" % ctx.prepared["inputs"].get(page, {}).get("what", page),
        src,
    )
    ctx.open_page(proj, page)
    lp, lib, el, ext, x, y, props = PLACEHOLDER_GENIE
    t = ctx.place((proj, page), lp, lib, el, ext, x, y, props)
    calls: list = [{"place": [lp, lib, el, ext, x, y, props], "type": t}]
    placed = _stage_page(ctx, unit, proj, page, "placed", "the genie placed", src)
    an, rec = _the_genie(placed)
    _select_an(ctx, an)
    ctx.com.PositionRotate()
    ctx.wait(0.5)
    ctx.save()
    calls.append({"PositionRotate": an, "record before": rec, "stage": "turned"})
    turned = _stage_page(ctx, unit, proj, page, "turned", "the genie turned", src)
    _select_an(ctx, an)
    call = _scale_selected(ctx, _page_record(turned, an), PLACEHOLDER_SCALE, "the genie")
    ctx.save()
    ctx.com.PageClose()
    call.update(AN=an, stage="scaled")
    calls.append(call)
    _stage_page(
        ctx,
        unit,
        proj,
        page,
        "scaled",
        "the turned genie scaled %g times about its box's top left" % PLACEHOLDER_SCALE,
        src,
    )
    path = _find_file(os.path.join(ctx.inst.user, lp), "%s.%s" % (lib, ext))
    if path is None:
        raise UnitFailed("%s has no %s.%s" % (lp, lib, ext))
    keep = _read(path)
    os.remove(path)
    calls.append({"removed": "%s/%s" % (lp, os.path.basename(path))})
    ctx.fresh_builder("the page's load + save with %s.%s missing" % (lib, ext))
    ctx.open_page(proj, page)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open + save": page, "library": "missing", "stage": "missing"})
    _stage_page(ctx, unit, proj, page, "missing", "its load + save with %s.%s missing" % (lib, ext), src)
    _write(path, keep)
    calls.append({"put back": "%s/%s" % (lp, os.path.basename(path))})
    ctx.fresh_builder("the page's load + save with %s.%s back" % (lib, ext))
    ctx.open_page(proj, page)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open + save": page, "library": "back", "stage": "back"})
    _stage_page(ctx, unit, proj, page, "back", "its load + save with %s.%s back" % (lib, ext), src)
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "%s/%s" % (proj, page), "calls": calls},
        "The COM calls of the unit in order, with what CtDraw's getters said (the "
        "stages name their folders).",
        src,
    )
    ctx.collect(unit, skip=_page_skip(proj, page))


def _place_in_element(
    ctx: Oracle,
    project: str,
    lib: str,
    element: str,
    lib_project: str,
    plib: str,
    pel: str,
    ext: str,
    x: int,
    y: int,
) -> int:
    """LibraryObjectPlaceEx into the open element window (its type tried 0 .. 4, cached per
    extension), saved; the element's block must then name ``plib.pel.<ext``."""
    from ctgedit.format.element import LibraryFile

    key = "place type " + ext
    order = list(range(5))
    if key in ctx.cache:
        order.remove(ctx.cache[key])
        order.insert(0, ctx.cache[key])
    want = ("%s.%s.<%s" % (plib, pel, ext)).lower().encode()
    for t in order:
        try:
            ctx.com.LibraryObjectPlaceEx(lib_project, plib, pel, t, ext == "ctl", x, y)
        except Exception:  # noqa: BLE001
            continue
        ctx.wait(0.5)
        ctx.save()
        lf = LibraryFile(ctx.read(project, lib) or b"")
        slot = lf.find(element)
        if slot is not None and want in lf.block_data(slot).lower():
            ctx.cache[key] = t
            return t
    raise UnitFailed("LibraryObjectPlaceEx placed no %s.%s in %s" % (plib, pel, element))


def _run_param_gained(ctx: Oracle, unit: Unit) -> None:
    """A genie without parameters pasted into a genie with one."""
    proj, lib = unit.data["project"], unit.data["library"]
    gp, glib, gel = PARAMLESS
    glib_file = glib + ".ctm"
    calls: list = []
    ctx.open_element(proj, lib, CHAIN_EDITED)
    t = _place_in_element(ctx, proj, lib, CHAIN_EDITED, gp, glib, gel, "ctm", *PARAMLESS_AT)
    ctx.com.PageClose()
    calls.append(
        {
            "open": CHAIN_EDITED,
            "LibraryObjectPlaceEx": [gp, glib, gel, t, False] + list(PARAMLESS_AT),
            "stage": "n0_pasted",
        }
    )
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "n0_pasted",
        [CHAIN_EDITED],
        "%s.%s placed into %s and %s saved" % (glib, gel, CHAIN_EDITED, CHAIN_EDITED),
    )
    _follow(ctx, unit, proj, lib, ("n1",), "pasted", calls)
    hosts = ctx.read(proj, lib)
    text, x, y = PARAMLESS_GAINED
    ctx.open_element(gp, glib_file, gel)
    before = _objects(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": gel, "DrawText": [text, x, y], "AN": an, "stage": "inner_gained"})
    gained = _emit_stage(
        ctx, unit, gp, glib_file, "inner_gained", [gel], "DrawText of %r in %s and its save" % (text, gel)
    )
    _follow(ctx, unit, proj, lib, (CHAIN_EDITED, "n1"), "same", calls)
    _put_file(ctx, proj, lib, hosts)
    _put_file(ctx, gp, glib_file, gained)
    ctx.fresh_builder(
        "the load + save of %s and n1 after %s gained a parameter, as a later session" % (CHAIN_EDITED, gel)
    )
    calls.append(
        {
            "fresh Graphics Builder": True,
            "libraries": "%s as after pasted/n1, %s as after inner_gained" % (lib, glib_file),
        }
    )
    _follow(ctx, unit, proj, lib, (CHAIN_EDITED, "n1"), "fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "pasted": "%s/%s/%s" % (gp, glib_file, gel), "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib), "%s/%s" % (gp, glib_file)})


def _run_label_space(ctx: Oracle, unit: Unit) -> None:
    """Q5: a placement whose value starts with a blank
    (fxnest n0, Label ' L1'): the box CtDraw stores at the placement's save, after a second
    save and after the page's load + save in a fresh Graphics Builder."""
    proj, page = unit.data["project"], unit.data["page"]
    src = "inputs/%s.ctg" % page
    data = ctx.put_input(proj, page)
    ctx.emit(
        unit,
        "input.ctg",
        data,
        "The page CtDraw opened: %s" % ctx.prepared["inputs"].get(page, {}).get("what", page),
        src,
    )
    ctx.open_page(proj, page)
    lp, lib, el, ext, x, y, props = LABEL_SPACE
    t = ctx.place((proj, page), lp, lib, el, ext, x, y, props)
    calls: list = [{"place": [lp, lib, el, ext, x, y, props], "type": t, "stage": "placed"}]
    _stage_page(ctx, unit, proj, page, "placed", "the placement and its save", src)
    ctx.save()
    calls.append({"save": page, "stage": "saved"})
    _stage_page(ctx, unit, proj, page, "saved", "a second save", src)
    ctx.com.PageClose()
    ctx.fresh_builder("the load + save of the page, as a later session")
    ctx.open_page(proj, page)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open + save": page, "fresh Graphics Builder": True, "stage": "reopened"})
    _stage_page(ctx, unit, proj, page, "reopened", "its load + save in a fresh Graphics Builder", src)
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "%s/%s" % (proj, page), "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        src,
    )
    ctx.collect(unit, skip=_page_skip(proj, page))


def _template_root_an(data: bytes) -> Tuple[int, str]:
    """(AN, link) of a page's template root (the record that links a .ctt)."""
    from ctgedit.format import genie_place as GP, geometry as G
    from ctgedit.format.page import parse_ctg_bytes

    for po in parse_ctg_bytes(data).page.objects:
        with contextlib.suppress(Exception):
            link = GP.decode(po).link or ""
            if link.lower().endswith(".<ctt"):
                return G.live_an(po), link
    raise UnitFailed("the page has no template root")


def _cut_link4(ctx: Oracle, unit: Unit, project: str, page: str, stage: str, calls: list) -> bytes:
    """Cut Link of the page's template (as _run_cut_link: Break Lock Mode on, the template
    root selected, menu command MENU_CUT_LINK answered Yes), saved: ``<stage>/page.ctg``.
    UnitFailed when the saved page still names the template or did not change."""
    source = ctx.read(project, page + ".ctg") or b""
    an, link = _template_root_an(source)
    ctx.open_page(project, page)
    turned_on = ctx.break_lock(True)
    try:
        selected = ctx.select_template(an)
        with ctx.answer("Yes"):
            ctx.menu_command(MENU_CUT_LINK)
            ctx.wait(2.0)
            ctx.save()
    finally:
        if turned_on:
            with contextlib.suppress(Exception):
                ctx.break_lock(False)
    ctx.com.PageClose()
    data = _stage_page(ctx, unit, project, page, stage, "Cut Link of its template and its save")
    left = link.lower().encode("cp1252") in data.lower()
    calls.append(
        {
            "Cut Link": page,
            "template": link,
            "AN": an,
            "selected": selected,
            "break_lock_posted": turned_on,
            "template_reference_left": left,
            "unchanged": data == source,
            "stage": stage,
        }
    )
    if left or data == source:
        raise UnitFailed(
            "Cut Link on %s (%s): %s"
            % (page, stage, "the page still names %s" % link if left else "nothing changed")
        )
    return data


def _run_cut_link_outer(ctx: Oracle, unit: Unit) -> None:
    """Cut Link on a page linked to an outer template."""
    style = OUTER_STYLE
    base, res, title = OUTER_BASE
    page = OUTER_LINKED_PAGE
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    link = ("%s.%s.<ctt" % (style, made)).encode()
    made_page = _new_page4(
        ctx, unit, style, made.split("@")[0], res, title, page, "page_made", calls, link, None
    )
    changed = _change_inner(ctx, unit, calls, made)
    _cut_link4(ctx, unit, "FxMain", page, "same/cut", calls)
    _put_file(ctx, "FxMain", page + ".ctg", made_page)
    ctx.fresh_builder("the load + save of %s after %s changed, as a later session" % (page, OUTER_BASE[0]))
    calls.append({"fresh Graphics Builder": True, "page": "as made", "library": "as after inner_changed"})
    ctx.open_page("FxMain", page)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open + save": page, "stage": "fresh/plain"})
    _stage_page(
        ctx,
        unit,
        "FxMain",
        page,
        "fresh/plain",
        "its load + save in a fresh Graphics Builder after the inner template changed",
    )
    _put_file(ctx, "FxMain", page + ".ctg", made_page)
    _put_file(ctx, OUTER_PROJECT, style + ".ctt", changed)
    ctx.fresh_builder("the Cut Link of %s after %s changed, as a later session" % (page, OUTER_BASE[0]))
    calls.append({"fresh Graphics Builder": True, "page": "as made", "library": "as after inner_changed"})
    _cut_link4(ctx, unit, "FxMain", page, "fresh/cut", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "FxMain/%s" % page, "made": made, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s.ctt" % (OUTER_PROJECT, style)} | _page_skip("FxMain", page))


# ---------------------------------------------------------------------------- the chain5 step
def _dialog_mark(ctx: Oracle) -> Tuple[int, int, int]:
    """Where the dialog watch's lists stand now (:func:`_dialogs_since` reads past it)."""
    w = ctx.watch
    if w is None:
        return 0, 0, 0
    return len(w.seen), len(w.dialogs), len(w.answered)


def _dialogs_since(ctx: Oracle, mark: Tuple[int, int, int]) -> dict:
    """The message boxes the watch answered, the other dialogs it saw and the ones it pressed
    OK on since ``mark`` (within one unit: the unit's end takes the message boxes)."""
    w = ctx.watch
    if w is None:
        return {}
    out = {}
    for key, seq, k in (
        ("message boxes", w.seen, mark[0]),
        ("dialogs", w.dialogs, mark[1]),
        ("answered OK", w.answered, mark[2]),
    ):
        if seq[k:]:
            out[key] = list(seq[k:])
    return out


def _com_put(ctx: Oracle, name: str, value) -> None:
    """A property put on the Graphics Builder (the selected object's ``name`` := ``value``),
    logged and watched as a call is."""
    t0 = time.time()
    ctx.clock.call = ("%s put" % name, t0)
    try:
        setattr(ctx.com._com, name, value)
    except Exception as e:  # noqa: BLE001
        ctx.log("COM put %s = %r -> error %s (%.2f s)" % (name, value, e, time.time() - t0))
        raise UnitFailed("the %s put of %r: %s" % (name, value, e)) from e
    finally:
        ctx.clock.call = None
    ctx.log("COM put %s = %r (%.2f s)" % (name, value, time.time() - t0))


def _put_text(ctx: Oracle, an: int, text: str) -> dict:
    """The caption of the text whose AN is ``an`` set to ``text``: the AttributeText put on
    the selected object once its class says it is a text (no AttributeText* getter)."""
    _select_an(ctx, an)
    cls = str(ctx.com.AttributeClass())
    if cls != "Text":
        raise UnitFailed("AN %d is a %s, not a text" % (an, cls))
    _com_put(ctx, "AttributeText", text)
    ctx.wait(0.5)
    return {"AN": an, "AttributeText put": text}


def _place_on_blank(ctx: Oracle, unit: Unit, page: str, args: tuple, stage: str, calls: list) -> bytes:
    """A blank FxMain page (prepare's ``page``), ``args`` (as PLACEHOLDER_GENIE) placed on it
    and saved: ``<stage>/page.ctg``. Returns the page."""
    proj = "FxMain"
    ctx.put_input(proj, page)
    ctx.open_page(proj, page)
    lp, lib, el, ext, x, y, props = args
    t = ctx.place((proj, page), lp, lib, el, ext, x, y, props)
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    calls.append({"place": [lp, lib, el, ext, x, y, props], "type": t, "page": page, "stage": stage})
    return _stage_page(
        ctx,
        unit,
        proj,
        page,
        stage,
        "%s.%s placed on the blank page and saved" % (lib, el),
        "inputs/%s.ctg" % page,
    )


def _load_save_page(
    ctx: Oracle, unit: Unit, project: str, page: str, stage: str, calls: list, what: str
) -> bytes:
    """CtDraw's PageOpen + PageSave of ``page``: ``<stage>/page.ctg``."""
    ctx.open_page(project, page)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open + save": page, "stage": stage})
    return _stage_page(ctx, unit, project, page, stage, what)


def _run_stale_host(ctx: Oracle, unit: Unit) -> None:
    """Q6: a host saved while the genie it nests is stale. n0 gains a
    text and is saved; n2 (which nests n1, which nests n0) is opened and saved while n1 has
    not been saved since; then n1 and n2 are; in the same Graphics Builder and again from
    n0's save in a fresh one."""
    proj, lib = unit.data["project"], unit.data["library"]
    text, x, y = CHAIN_GAINED
    calls: list = []
    ctx.open_element(proj, lib, CHAIN_EDITED)
    before = _objects(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": CHAIN_EDITED, "DrawText": [text, x, y], "AN": an, "stage": "n0_gained"})
    saved = _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "n0_gained",
        [CHAIN_EDITED],
        "CtDraw's DrawText of %r in %s and its save" % (text, CHAIN_EDITED),
    )
    for where in ("same", "fresh"):
        if where == "fresh":
            _put_file(ctx, proj, lib, saved)
            ctx.fresh_builder(
                "the load + save of n2 over a stale n1 from %s's save, as a later session" % CHAIN_EDITED
            )
            calls.append({"fresh Graphics Builder": True, "library": "as after n0_gained"})
        _follow(ctx, unit, proj, lib, ("n2",), "%s/stale" % where, calls)
        _follow(ctx, unit, proj, lib, ("n1", "n2"), "%s/after" % where, calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


def _run_param_default(ctx: Oracle, unit: Unit) -> None:
    """Q7: a parameter default that changes. n0's text becomes
    PARAM_DEFAULTS[0], n0 is saved, n1 opened and saved and placed on a blank page; then the
    same with PARAM_DEFAULTS[1]; then, from n0's second save (n1 still holding the first),
    n1's load + save and a placement in a fresh Graphics Builder."""
    proj, lib = unit.data["project"], unit.data["library"]
    key = "%s/%s/%s" % (proj, lib, CHAIN_EDITED)
    calls: list = []
    saved = b""
    for text in PARAM_DEFAULTS:
        tag = "q" + text.rsplit("=", 1)[1].strip("%")
        ctx.open_element(proj, lib, CHAIN_EDITED)
        an = _select_text(ctx, key)
        call = _put_text(ctx, an, text)
        ctx.save()
        ctx.com.PageClose()
        call.update(open=CHAIN_EDITED, stage="%s/n0" % tag)
        calls.append(call)
        saved = _emit_stage(
            ctx,
            unit,
            proj,
            lib,
            "%s/n0" % tag,
            [CHAIN_EDITED],
            "%s's text set to %r and %s saved" % (CHAIN_EDITED, text, CHAIN_EDITED),
        )
        _follow(ctx, unit, proj, lib, ("n1",), tag, calls)
        _place_on_blank(ctx, unit, "FxOraclePlace", PARAM_DEFAULT_PLACE, "%s/page" % tag, calls)
    _put_file(ctx, proj, lib, saved)
    ctx.fresh_builder(
        "n1's load + save and its placement after %s's default changed, as a later session" % CHAIN_EDITED
    )
    calls.append({"fresh Graphics Builder": True, "library": "as after q7/n0"})
    _follow(ctx, unit, proj, lib, ("n1",), "q7_fresh", calls)
    _place_on_blank(ctx, unit, "FxOraclePlace", PARAM_DEFAULT_PLACE, "q7_fresh/page", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", "FxOraclePlace"))


def _run_label_edit(ctx: Oracle, unit: Unit) -> None:
    """Q9 (a): a page places fx_value (its text '@(%Description%)')
    with Description 'Fx Start'; the genie's text changes and the genie is saved; the page's
    load + save (same Graphics Builder, then a fresh one from the page as placed): the labels
    of its table."""
    page = unit.data["page"]
    calls: list = []
    placed = _place_on_blank(ctx, unit, page, LABEL_GENIE, "placed", calls)
    lp, glib, gel = LABEL_GENIE[:3]
    lib = glib + ".ctm"
    ctx.open_element(lp, lib, gel)
    texts = _texts(ctx)
    if len(texts) != 1:
        raise UnitFailed("%s shows %d texts (%s), not one" % (gel, len(texts), texts))
    call = _put_text(ctx, texts[0], LABEL_EDITED)
    ctx.save()
    ctx.com.PageClose()
    call.update(open=gel, stage="genie_edited")
    calls.append(call)
    _emit_stage(
        ctx,
        unit,
        lp,
        lib,
        "genie_edited",
        [gel],
        "%s's text set to %r and %s saved" % (gel, LABEL_EDITED, gel),
        source="FxLib/%s" % lib,
    )
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "same",
        calls,
        "its load + save after %s changed, in the same Graphics Builder" % gel,
    )
    _put_file(ctx, "FxMain", page + ".ctg", placed)
    ctx.fresh_builder("the page's load + save after %s changed, as a later session" % gel)
    calls.append({"fresh Graphics Builder": True, "page": "as placed"})
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "fresh",
        calls,
        "its load + save after %s changed, in a fresh Graphics Builder" % gel,
    )
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "FxMain/%s" % page, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
    )
    ctx.collect(unit, skip={"%s/%s" % (lp, lib)} | _page_skip("FxMain", page))


def _run_label_states(ctx: Oracle, unit: Unit) -> None:
    """Q9 (b): texts with a multi-state, an array and an On / Off
    display value whose rows are '@()' labels, drawn in a genie and saved: the labels of the
    element's import table and their order; its load + save in a fresh Graphics Builder and
    a placement on a page."""
    lp, glib, gel = STATE_GENIE
    lib = glib + ".ctm"
    calls: list = []
    ctx.open_element(lp, lib, gel)
    for text, x, y, kind, exprs, rows in STATE_TEXTS:
        before = _objects(ctx)
        ctx.com.DrawText(text, x, y)
        ctx.wait(0.5)
        an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
        _select_an(ctx, an)
        ctx.com.PropertiesDisplayValuePut(kind, *exprs)
        for i, row in enumerate(rows):
            ctx.com.PropertiesDisplayValueTextPut(i, row)
        ctx.wait(0.5)
        got: dict = {}
        try:
            v = ctx.com.PropertiesDisplayValueGet()
            got["PropertiesDisplayValueGet"] = list(v) if isinstance(v, (tuple, list)) else v
        except Exception as e:  # noqa: BLE001
            got["PropertiesDisplayValueGet"] = "error: %s" % (e,)
        back = []
        for i in range(len(rows)):
            try:
                back.append(ctx.com.PropertiesDisplayValueTextGet(i))
            except Exception as e:  # noqa: BLE001
                back.append("error: %s" % (e,))
        got["PropertiesDisplayValueTextGet"] = back
        calls.append(
            {
                "DrawText": [text, x, y],
                "AN": an,
                "PropertiesDisplayValuePut": [kind] + list(exprs),
                "PropertiesDisplayValueTextPut": list(rows),
                "getters": got,
                "stage": "saved",
            }
        )
    ctx.save()
    ctx.com.PageClose()
    _emit_stage(
        ctx,
        unit,
        lp,
        lib,
        "saved",
        [gel],
        "the texts drawn in %s with their display values and %s saved" % (gel, gel),
    )
    ctx.fresh_builder("the load + save of %s, as a later session" % gel)
    _follow(ctx, unit, lp, lib, (gel,), "reopened", calls)
    _place_on_blank(ctx, unit, unit.data["page"], STATE_PLACE, "page", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (lp, lib), "calls": calls},
        "The COM calls of the unit in order, with what CtDraw's getters said (the "
        "stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (lp, lib)} | _page_skip("FxMain", unit.data["page"]))


def _without_bare_entries(data: bytes, lib: str, element: str) -> Tuple[bytes, List[str]]:
    """The library with ``element``'s import table without its bare entries (a zero GUID,
    not a label), as ctgedit's element save drops them; and the names dropped."""
    from ctgedit.format import links
    from ctgedit.format.element import LibraryFile, element_content, open_element, serialize_element

    pe = open_element(lib, element, library=LibraryFile(data))
    t = links.parse_link_table(pe.page.link_table_raw) if pe.page.link_count else None
    if t is None:
        return data, []
    area, ents = t
    keep = [(g, n) for g, n in ents if not (g == bytes(16) and not n.startswith("\\"))]
    dropped = [n for g, n in ents if (g, n) not in keep]
    raw, count = links.serialize_link_table(area, keep)
    pe.page.link_table_raw = raw
    pe.page.link_count = count
    return serialize_element(pe, element_content(pe)), dropped


def _run_element_placeholder(ctx: Oracle, unit: Unit) -> None:
    """Q10: an element that holds a placeholder. fx_row's nested
    library missing, CtDraw's open + save of fx_row; a page placing it, its load + save with
    the library missing and back, a placement with it back; fx_row's load + save with the
    library back, as saved and without its bare zero-GUID entry."""
    from ctgedit.format.element import LibraryFile

    lp, hlib, hel, ext, _x, _y, _props = PLACEHOLDER_HOST
    hfile = "%s.%s" % (hlib, ext)
    mp, mlib = PLACEHOLDER_MISSING
    page = unit.data["page"]
    calls: list = []
    path = _find_file(os.path.join(ctx.inst.user, mp), mlib)
    if path is None:
        raise UnitFailed("%s has no %s" % (mp, mlib))
    keep = _read(path)
    os.remove(path)
    calls.append({"removed": "%s/%s" % (mp, os.path.basename(path))})
    ctx.open_element(lp, hfile, hel)
    ctx.save()
    ctx.com.PageClose()
    held = _emit_stage(
        ctx,
        unit,
        lp,
        hfile,
        "element_missing",
        [hel],
        "%s's open + save with %s missing" % (hel, mlib),
        source="FxLib/%s" % hfile,
    )
    lf = LibraryFile(held)
    has = b"_library" in lf.block_data(lf.find(hel)).lower()
    calls.append({"open + save": hel, "library": "missing", "placeholder": has, "stage": "element_missing"})
    if not has:
        ctx.emit_json(unit, "actions.json", {"calls": calls}, "The COM calls of the unit.")
        raise UnitFailed("CtDraw's save of %s with %s missing wrote no placeholder" % (hel, mlib))
    _place_on_blank(ctx, unit, page, PLACEHOLDER_HOST, "page_placed", calls)
    ctx.fresh_builder("the page's load + save with %s still missing" % mlib)
    _load_save_page(
        ctx, unit, "FxMain", page, "page_missing", calls, "its load + save with %s still missing" % mlib
    )
    _write(path, keep)
    calls.append({"put back": "%s/%s" % (mp, os.path.basename(path))})
    ctx.fresh_builder("the page's load + save with %s back" % mlib)
    _load_save_page(ctx, unit, "FxMain", page, "page_back", calls, "its load + save with %s back" % mlib)
    _place_on_blank(ctx, unit, "FxOracleDraw", PLACEHOLDER_HOST, "page_placed_back", calls)
    _put_file(ctx, lp, hfile, held)
    ctx.fresh_builder("%s's load + save with %s back" % (hel, mlib))
    calls.append({"library": "%s as after element_missing" % hfile})
    _follow(ctx, unit, lp, hfile, (hel,), "element_back", calls)
    stripped, dropped = _without_bare_entries(held, hfile, hel)
    if not dropped:
        raise UnitFailed("%s's table has no bare entry to remove" % hel)
    _put_file(ctx, lp, hfile, stripped)
    _emit_stage(
        ctx,
        unit,
        lp,
        hfile,
        "nobare_input",
        [hel],
        "%s as after element_missing without the bare entries %s (made by the tool)" % (hel, dropped),
    )
    ctx.fresh_builder("%s's load + save without its bare entry" % hel)
    calls.append({"library": "%s as nobare_input" % hfile, "dropped": dropped})
    _follow(ctx, unit, lp, hfile, (hel,), "element_nobare", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "FxMain/%s" % page, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
    )
    ctx.collect(
        unit, skip={"%s/%s" % (lp, hfile), "%s/%s" % (mp, mlib)} | _page_skip("FxMain", page, "FxOracleDraw")
    )


def _run_order(ctx: Oracle, unit: Unit) -> None:
    """Q11: a page linked to normal@XGA (which nests fx_value) that also
    places fx_value; fx_value gains two rectangles and normal@XGA is saved again; the page's
    load + save (same Graphics Builder, then a fresh one): the handles and ANs of the new
    records."""
    style = OUTER_STYLE
    base, res, title = OUTER_BASE
    inner = _template_name(base, "XGA", title)
    page = unit.data["page"]
    calls: list = []
    _new_page_from(
        ctx,
        unit,
        style,
        base,
        res,
        title,
        page,
        "new_page",
        calls,
        want=("%s.%s.<ctt" % (style, inner)).encode(),
    )
    ctx.open_page("FxMain", page)
    lp, glib, gel, ext, x, y, props = ORDER_GENIE
    t = ctx.place(("FxMain", page), lp, glib, gel, ext, x, y, props)
    ctx.com.PageClose()
    calls.append({"place": [lp, glib, gel, ext, x, y, props], "type": t, "stage": "page_made"})
    made = _stage_page(ctx, unit, "FxMain", page, "page_made", "%s.%s placed on it" % (glib, gel))
    lib = glib + ".ctm"
    ctx.open_element(lp, lib, gel)
    for r in ORDER_RECTS:
        ctx.com.DrawRectangle(*r)
        ctx.wait(0.5)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": gel, "DrawRectangle": [list(r) for r in ORDER_RECTS], "stage": "genie_changed"})
    _emit_stage(
        ctx,
        unit,
        lp,
        lib,
        "genie_changed",
        [gel],
        "two rectangles drawn on %s and its save" % gel,
        source="FxLib/%s" % lib,
    )
    tlib = style + ".ctt"
    ctx.open_element(OUTER_PROJECT, tlib, inner)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open + save": inner, "stage": "template_saved"})
    _emit_stage(
        ctx,
        unit,
        OUTER_PROJECT,
        tlib,
        "template_saved",
        [inner],
        "%s's open + save after %s changed" % (inner, gel),
        source="FxStyle/%s" % tlib,
    )
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "same",
        calls,
        "its load + save after %s and %s changed, in the same Graphics Builder" % (gel, inner),
    )
    _put_file(ctx, "FxMain", page + ".ctg", made)
    ctx.fresh_builder("the page's load + save after %s and %s changed, as a later session" % (gel, inner))
    calls.append({"fresh Graphics Builder": True, "page": "as page_made"})
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "fresh",
        calls,
        "its load + save after %s and %s changed, in a fresh Graphics Builder" % (gel, inner),
    )
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "FxMain/%s" % page, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
    )
    ctx.collect(
        unit, skip={"%s/%s" % (lp, lib), "%s/%s" % (OUTER_PROJECT, tlib)} | _page_skip("FxMain", page)
    )


def _run_shipped_nested(ctx: Oracle, unit: Unit) -> None:
    """Q12: a page placing shipped genies whose own tables quote older
    nested stamps; its load + save in a fresh Graphics Builder; the shipped files checked
    unchanged. Its output holds AVEVA content: it refuses the committed oracle folder."""
    if _inside(ctx.out_root, ORACLE):
        raise UnitFailed(
            "this unit captures AVEVA content: run it with --out outside tests\\fixtures\\oracle"
        )
    proj, page = unit.data["project"], unit.data["page"]
    real: Dict[str, Tuple[str, Optional[str]]] = {}
    for name in SHIPPED5_FILES:
        p = _find_file(os.path.join(ctx.inst.user, "Include"), name)
        if p is not None:
            real[name] = (p, file_sha1(p))
    calls: list = [{"shipped sha1 before": {n: s for n, (_p, s) in real.items()}}]
    changed: List[str] = []
    try:
        ctx.put_input(proj, page)
        ctx.open_page(proj, page)
        for args in SHIPPED5_PLACES:
            lp, lib, el, ext, x, y, props = args
            try:
                t = ctx.place((proj, page), lp, lib, el, ext, x, y, props)
                calls.append({"place": list(args), "type": t, "stage": "placed"})
            except UnitFailed as e:
                calls.append({"place": list(args), "error": str(e), "stage": "placed"})
        with contextlib.suppress(Exception):
            ctx.com.PageClose()
        _stage_page(ctx, unit, proj, page, "placed", "the shipped genies placed")
        ctx.fresh_builder("the page's load + save, as a later session")
        _load_save_page(
            ctx, unit, proj, page, "reopened", calls, "its load + save in a fresh Graphics Builder"
        )
    finally:
        changed = [n for n, (p, s) in real.items() if file_sha1(p) != s]
        calls.append({"shipped files changed": changed})
        ctx.emit_json(
            unit,
            "actions.json",
            {"page": "%s/%s" % (proj, page), "calls": calls},
            "The COM calls of the unit in order (the stages name their folders).",
        )
    if changed:
        raise UnitFailed("the install's Include %s changed during the unit" % ", ".join(changed))
    ctx.collect(unit, skip=_page_skip(proj, page))


def _copy_member_an(data: bytes, lib: str, element: str, rect: Sequence[int]) -> int:
    """The live AN of the one record of ``element`` (below its root) whose box is ``rect``."""
    from ctgedit.format import genie_place as GP, geometry as G
    from ctgedit.format.element import LibraryFile, open_element

    pe = open_element(lib, element, library=LibraryFile(data))
    hits = [po for po in pe.tree[1:] if GP.decode(po).rect1 == tuple(rect)]
    if len(hits) != 1:
        raise UnitFailed("%s has %d records with the box %r" % (element, len(hits), rect))
    return G.live_an(hits[0])


def _select_member(ctx: Oracle, an: int) -> str:
    """Select the template copy's member whose AN is ``an`` (Break Lock Mode on):
    PageSelectObject, else the template objects in turn (PageTemplateSelectFirstObject /
    PageTemplateSelectNextObject). Returns which one did."""
    with contextlib.suppress(Exception):
        ctx.com.PageSelectObject(an)
        if int(ctx.com.AttributeAN()) == an:
            return "PageSelectObject(%d)" % an
    seen = []
    try:
        ctx.com.PageTemplateSelectFirstObject()
        for _n in range(200):
            got = int(ctx.com.AttributeAN())
            if got == an:
                return "PageTemplateSelectFirstObject / NextObject (%d steps)" % len(seen)
            if got in seen:
                break
            seen.append(got)
            ctx.com.PageTemplateSelectNextObject()
    except Exception as e:  # noqa: BLE001
        ctx.log("The template objects: %s" % e)
    raise UnitFailed("the copy's member AN %d could not be selected (template objects %s)" % (an, seen))


def _run_extra_binding(ctx: Oracle, unit: Unit) -> None:
    """Q13: an outer template (page form) whose copy's member carries a
    binding the inner template's record lacks (a touch command put on the copy's rectangle,
    Break Lock Mode on); the inner template gains a rectangle and is saved; the outer's load
    + save in the same Graphics Builder and in a fresh one."""
    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    an = _copy_member_an(ctx.read(proj, lib) or b"", lib, made, BOUND_MEMBER)
    ctx.open_element(proj, lib, made)
    turned_on = ctx.break_lock(True)
    try:
        how = _select_member(ctx, an)
        ctx.com.PropertiesInputTouchPut(*BOUND_TOUCH)
        ctx.wait(0.5)
        ctx.save()
    finally:
        if turned_on:
            with contextlib.suppress(Exception):
                ctx.break_lock(False)
    ctx.com.PageClose()
    calls.append(
        {
            "open": made,
            "member AN": an,
            "selected": how,
            "PropertiesInputTouchPut": list(BOUND_TOUCH),
            "break_lock_posted": turned_on,
            "stage": "bound",
        }
    )
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "bound",
        [made],
        "a touch command put on the copy's member AN %d of %s and its save" % (an, made),
    )
    changed = _change_inner(ctx, unit, calls, made)
    _follow(ctx, unit, proj, lib, (made,), "same", calls)
    _put_file(ctx, proj, lib, changed)
    ctx.fresh_builder("the load + save of %s after %s changed, as a later session" % (made, OUTER_BASE[0]))
    calls.append({"fresh Graphics Builder": True, "library": "as after inner_changed"})
    _follow(ctx, unit, proj, lib, (made,), "fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "made": made, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


def _with_marker_box(data: bytes, lib: str, element: str, box: tuple) -> Tuple[bytes, int]:
    """The library with the one DummyMarker below ``element``'s root given ``box`` (anchor,
    rect1, rect2); and its index."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.element import LibraryFile, element_content, open_element, serialize_element

    pe = open_element(lib, element, library=LibraryFile(data))
    tree = list(pe.tree)
    hits = [k for k in range(1, len(tree)) if tree[k].type_code == 0xFFFF]
    if len(hits) != 1:
        raise UnitFailed("%s holds %d DummyMarkers, not one" % (element, len(hits)))
    r = GP.decode(tree[hits[0]])
    r.anchor, r.rect1, r.rect2 = (tuple(v) for v in box)
    tree[hits[0]] = GP.to_po(GP.encode(r))
    return serialize_element(pe, element_content(pe, objects=tree)), hits[0]


def _run_marker_box(ctx: Oracle, unit: Unit) -> None:
    """Q14: the outer template's copy in the element form (the outer
    given a parameter of its own), its free DummyMarker given the box the inner template
    stores; the outer's load + save in a fresh Graphics Builder: the box derived again or
    kept."""
    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    text, x, y = OUTER_PARAM
    ctx.open_element(proj, lib, made)
    before = _objects(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": made, "DrawText": [text, x, y], "AN": an, "stage": "param_added"})
    added = _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "param_added",
        [made],
        "DrawText of %r on %s and its save (the copy in the element form)" % (text, made),
    )
    patched, k = _with_marker_box(added, lib, made, MARKER_BOX)
    _put_file(ctx, proj, lib, patched)
    calls.append({"marker": k, "box": [list(v) for v in MARKER_BOX], "stage": "patched"})
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "patched",
        [made],
        "the copy's DummyMarker (record %d) given the box %r (made by the tool)" % (k, MARKER_BOX[1]),
    )
    ctx.fresh_builder("the load + save of %s with the marker's box as stored, as a later session" % made)
    _follow(ctx, unit, proj, lib, (made,), "plain", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "made": made, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


def _wait_quiet(ctx: Oracle, limit: float = 240.0, quiet: float = 3.0) -> dict:
    """After a posted command: wait until CtDraw has shown no dialog for ``quiet`` seconds
    (the watch answers the message boxes); the dialogs still open at ``limit`` are closed
    with IDCANCEL. The dialogs seen (title, texts, when) and the time waited."""
    from ctgedit.backend import winui
    import win32con
    import win32gui

    seen: Dict[int, dict] = {}
    t0 = last = time.time()
    while True:
        now = time.time()
        hs = []
        with contextlib.suppress(Exception):
            for pid in winui.pids_of("CtDraw32.exe"):
                hs.extend(winui.windows_of(pid))
        if hs:
            last = now
            for h in hs:
                if h not in seen:
                    kids = []
                    with contextlib.suppress(Exception):
                        kids = winui.children(h)
                    seen[h] = {
                        "title": winui.text(h),
                        "first seen": round(now - t0, 1),
                        "texts": [k["text"] for k in kids if (k["text"] or "").strip()][:40],
                    }
                seen[h]["last seen"] = round(now - t0, 1)
        if now - last >= quiet * max(ctx.pause, 0.01):
            break
        if now - t0 > limit:
            for h in hs:
                with contextlib.suppress(Exception):
                    win32gui.PostMessage(h, win32con.WM_COMMAND, win32con.IDCANCEL, 0)
                seen[h]["cancelled"] = True
            break
        time.sleep(0.5)
    return {"dialogs": list(seen.values()), "seconds": round(time.time() - t0, 1)}


def _run_template_warning(ctx: Oracle, unit: Unit) -> None:
    """The Save Template warning."""
    from ctgedit.format.element import LibraryFile

    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    inner = _template_name(base, "XGA", title)
    page = OUTER_LINKED_PAGE
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    link = ("%s.%s.<ctt" % (style, made)).encode()
    _new_page4(ctx, unit, style, made.split("@")[0], res, title, page, "page_made", calls, link, None)

    def state():
        data = ctx.read(proj, lib) or b""
        lf = LibraryFile(data)
        blocks = {el: lf.block_data(lf.find(el)) for el in (inner, made)}
        return blocks, {p: ctx.read("FxMain", p + ".ctg") for p in WARNING_PAGES}

    def stage_out(stage: str, what: str) -> dict:
        blocks, pages = state()
        _emit_stage(ctx, unit, proj, lib, stage, [inner, made], what)
        for p in WARNING_PAGES:
            if pages[p] is not None:
                _stage_page(ctx, unit, "FxMain", p, "%s/%s" % (stage, p), what)
        return {
            "inner saved": blocks[inner] != blocks0[inner],
            "outer saved": blocks[made] != blocks0[made],
            "pages changed": {p: pages[p] != pages0[p] for p in WARNING_PAGES},
        }

    blocks0, pages0 = state()
    ctx.open_element(proj, lib, inner)
    ctx.com.DrawRectangle(*INNER_RECT)
    ctx.wait(0.5)
    with contextlib.suppress(Exception):
        ctx.com.PageSelectObject(0)
    mark = _dialog_mark(ctx)
    with ctx.answer("Yes"):
        ctx.menu_command(MENU_SAVE)
        waited = _wait_quiet(ctx)
        name = ctx.page_name()  # (a call: it waits for CtDraw to be idle)
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    got = stage_out(
        "menu_save",
        "a rectangle drawn on %s and File > Save (menu %d), the "
        "message boxes answered Yes" % (inner, MENU_SAVE),
    )
    calls.append(
        {
            "open": inner,
            "DrawRectangle": list(INNER_RECT),
            "menu": MENU_SAVE,
            "PageName after": name,
            "waited": waited,
            "watch": _dialogs_since(ctx, mark),
            "after": got,
            "stage": "menu_save",
        }
    )
    if not got["inner saved"]:
        raise UnitFailed("File > Save did not save %s" % inner)
    if not any(got["pages changed"].values()):
        ctx.project("FxMain")
        mark = _dialog_mark(ctx)
        t0 = time.time()
        with ctx.answer("Yes"):
            try:
                r = ctx.com.ProjectUpdatePages(True)
                result = "returned %r" % (r,)
            except Exception as e:  # noqa: BLE001
                result = "error: %s" % (e,)
            waited = _wait_quiet(ctx, quiet=2.0)
        got = stage_out("update_pages", "Tools > Update Pages over COM (ProjectUpdatePages True) on FxMain")
        calls.append(
            {
                "ProjectUpdatePages": [True],
                "project": "FxMain",
                "result": result,
                "seconds": round(time.time() - t0, 1),
                "waited": waited,
                "watch": _dialogs_since(ctx, mark),
                "after": got,
                "stage": "update_pages",
            }
        )
    files = sorted(rel for rel, _b, _a in ctx.changes())
    ctx.emit_json(
        unit,
        "actions.json",
        {
            "library": "%s/%s" % (proj, lib),
            "made": made,
            "page": "FxMain/%s" % page,
            "calls": calls,
            "files changed": files,
        },
        "The COM calls of the unit in order (the stages name their folders) and "
        "every file the unit changed (not collected: an Update Pages run writes "
        "many pages and their rows).",
        unit.sources[0],
    )


def _group_of(data: bytes, member_an: int) -> int:
    """The live AN of the root group of a saved page that holds the record ``member_an``."""
    from ctgedit.format import genie_place as GP, geometry as G
    from ctgedit.format.page import parse_ctg_bytes

    objs = parse_ctg_bytes(data).page.objects
    k = 0
    while k < len(objs):
        end = GP.subtree_end(objs, k)
        if any(G.live_an(po) == member_an for po in objs[k + 1 : end]):
            return G.live_an(objs[k])
        k = end
    raise UnitFailed("no root group holds AN %d" % member_an)


def _run_scaled_groups(ctx: Oracle, unit: Unit) -> None:
    """Groups scaled."""
    proj, page = unit.data["project"], unit.data["page"]
    src = "inputs/%s.ctg" % page
    data = ctx.put_input(proj, page)
    ctx.emit(
        unit,
        "input.ctg",
        data,
        "The page CtDraw opened: %s" % ctx.prepared["inputs"].get(page, {}).get("what", page),
        src,
    )
    ctx.open_page(proj, page)
    calls: list = []
    members: Dict[str, int] = {}
    for text, x, y, mirror, rect in GROUP_TEXTS:
        before = _objects(ctx)
        ctx.com.DrawText(text, x, y)
        ctx.wait(0.5)
        t_an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
        if mirror:
            _select_an(ctx, t_an)
            ctx.com.PositionMirrorHorizontal()
            ctx.wait(0.5)
        before = _objects(ctx)
        ctx.com.DrawRectangle(*rect)
        ctx.wait(0.5)
        r_an = _drawn(ctx, before, "SquareV3", "DrawRectangle")
        ctx.com.PageSelectObject(t_an)
        ctx.com.PageSelectObjectAdd(r_an)
        ctx.com.PageGroupSelectedObjects()
        ctx.wait(0.5)
        got = {}
        with contextlib.suppress(Exception):
            got = {"AN": int(ctx.com.AttributeAN()), "class": str(ctx.com.AttributeClass())}
        members[text] = t_an
        calls.append(
            {
                "group": text,
                "text AN": t_an,
                "rectangle AN": r_an,
                "mirrored": mirror,
                "DrawText": [text, x, y],
                "DrawRectangle": list(rect),
                "selected after PageGroupSelectedObjects": got,
                "stage": "grouped",
            }
        )
    fonts: Dict[str, int] = {}
    for text, x, y, mirror in FONT_TEXTS:
        before = _objects(ctx)
        ctx.com.DrawText(text, x, y)
        ctx.wait(0.5)
        fonts[text] = an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
        if mirror:
            _select_an(ctx, an)
            ctx.com.PositionMirrorHorizontal()
            ctx.wait(0.5)
        calls.append({"DrawText": [text, x, y], "AN": an, "mirrored": mirror, "stage": "grouped"})
    ctx.save()
    saved = _stage_page(ctx, unit, proj, page, "grouped", "the groups and the texts drawn", src)
    for text, t_an in members.items():
        g_an = _group_of(saved, t_an)
        _select_an(ctx, g_an)
        call = _scale_selected(ctx, _page_record(saved, g_an), RESIZE_SCALE, text)
        call.update(AN=g_an, stage="scaled")
        calls.append(call)
    for text, an in fonts.items():
        _select_an(ctx, an)
        if str(ctx.com.AttributeClass()) != "Text":
            raise UnitFailed("AN %d is not a text" % an)
        before = _probe(ctx)
        _com_put(ctx, "AttributeTextFontSize", FONT_SIZE)
        ctx.wait(0.5)
        calls.append(
            {
                "AttributeTextFontSize put": FONT_SIZE,
                "text": text,
                "AN": an,
                "record before": _page_record(saved, an),
                "getters before": before,
                "getters after": _probe(ctx),
                "stage": "scaled",
            }
        )
    ctx.save()
    _stage_page(
        ctx,
        unit,
        proj,
        page,
        "scaled",
        "each group scaled %g times about its box's top left and the texts' font size put" % RESIZE_SCALE,
        src,
    )
    ctx.com.PageClose()
    ctx.fresh_builder("the load + save of the scaled page, as a later session")
    _load_save_page(ctx, unit, proj, page, "reopened", calls, "its load + save in a fresh Graphics Builder")
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "%s/%s" % (proj, page), "calls": calls},
        "The COM calls of the unit in order, with what CtDraw's getters said (the "
        "stages name their folders).",
        src,
    )
    ctx.collect(unit, skip=_page_skip(proj, page))


def _run_cycles(ctx: Oracle, unit: Unit) -> None:
    """A genie placed into itself, and into a genie it nests."""
    from ctgedit.format.element import LibraryFile

    proj, lib = unit.data["project"], unit.data["library"]
    stem = os.path.splitext(lib)[0]
    source = ctx.read(proj, lib)
    calls: list = []
    for k, (el, x, y) in enumerate(CYCLE_PLACES):
        stage = "%s_into_%s" % (el, CHAIN_EDITED)
        if k:
            _put_file(ctx, proj, lib, source)
            ctx.fresh_builder(
                "the next placement into %s, from the library as it was" % CHAIN_EDITED,
                force=ctx.builder_gone(),
            )
        ctx.open_element(proj, lib, CHAIN_EDITED)
        rec: dict = {
            "open": CHAIN_EDITED,
            "LibraryObjectPlaceEx": [proj, stem, el, 0, False, x, y],
            "objects before": _objects(ctx),
            "stage": stage,
        }
        mark = _dialog_mark(ctx)
        t0 = time.time()
        try:
            r = ctx.com.LibraryObjectPlaceEx(proj, stem, el, 0, False, x, y)
            rec["result"] = "returned %r" % (r,)
        except Exception as e:  # noqa: BLE001
            rec["result"] = "error: %s" % (e,)
        rec["seconds"] = round(time.time() - t0, 1)
        ctx.wait(1.0)
        rec["builder ended"] = ctx.builder_gone()
        if not rec["builder ended"]:
            with contextlib.suppress(Exception):
                rec["objects after"] = _objects(ctx)
            t0 = time.time()
            try:
                ctx.save()
                rec["save"] = "returned"
            except Exception as e:  # noqa: BLE001
                rec["save"] = "error: %s" % (e,)
            rec["save seconds"] = round(time.time() - t0, 1)
            rec["builder ended after the save"] = ctx.builder_gone()
            with contextlib.suppress(Exception):
                ctx.com.PageClose()
        rec.update(_dialogs_since(ctx, mark))
        data = ctx.read(proj, lib)
        rec["library changed"] = data != source
        if data is not None and data != source:
            lf = LibraryFile(data)
            block = lf.block_data(lf.find(CHAIN_EDITED)).lower()
            rec["n0 names %s" % el] = ("%s.%s.<ctm" % (stem, el)).encode() in block
            _emit_stage(
                ctx,
                unit,
                proj,
                lib,
                stage,
                [CHAIN_EDITED],
                "%s placed into %s and saved" % (el, CHAIN_EDITED),
            )
        calls.append(rec)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order, how long each took and what CtDraw "
        "answered (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


# ---------------------------------------------------------------------------- the chain6 step
def _scale_xy(ctx: Oracle, rec: dict, sx: float, sy: float, what: str) -> dict:
    """A scale by ``sx`` across and ``sy`` down about the selected object's box's top left
    (``rec``: its record as last saved) composed onto its matrix, as :func:`_scale_selected`
    composes a uniform one: the AttributeTransformationMatrixPut of S o M (the x factor on
    A, C and H, the y factor on B, D and K). The getters before and after."""
    l, t = rec["rect1"][0], rec["rect1"][1]
    before = _probe(ctx)
    cur = before.get("AttributeTransformationMatrixGet")
    if not isinstance(cur, list) or len(cur) != 6:
        cur = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    a, b, c, d, h, k = (float(v) for v in cur)
    m = [sx * a, sy * b, sx * c, sy * d, sx * h + l - sx * l, sy * k + t - sy * t]
    ctx.com.AttributeTransformationMatrixPut(*m)
    ctx.wait(0.5)
    return {
        "scaled": what,
        "factors": [sx, sy],
        "composed onto": [a, b, c, d, h, k],
        "AttributeTransformationMatrixPut": m,
        "record before": rec,
        "getters before": before,
        "getters after": _probe(ctx),
    }


def _run_text_resize(ctx: Oracle, unit: Unit) -> None:
    """Q17: texts resized unevenly and mirrored back. On a blank page
    four texts drawn, two of them mirrored; each scaled about its box's top left by its own
    x and y factors (the matrix put composed onto its matrix); the mirrored ones mirrored
    back; each step saved; the page's load + save in a fresh Graphics Builder."""
    proj, page = unit.data["project"], unit.data["page"]
    src = "inputs/%s.ctg" % page
    data = ctx.put_input(proj, page)
    ctx.emit(
        unit,
        "input.ctg",
        data,
        "The page CtDraw opened: %s" % ctx.prepared["inputs"].get(page, {}).get("what", page),
        src,
    )
    ctx.open_page(proj, page)
    calls: list = []
    made: Dict[str, int] = {}
    for text, x, y, _m, _s in TEXT_RESIZE:
        before = _objects(ctx)
        ctx.com.DrawText(text, x, y)
        ctx.wait(0.5)
        made[text] = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    ctx.save()
    calls.append({"DrawText": [list(t[:3]) for t in TEXT_RESIZE], "ANs": made, "stage": "drawn"})
    _stage_page(ctx, unit, proj, page, "drawn", "the texts drawn", src)

    def mirror(stage: str, what: str) -> bytes:
        for text, _x, _y, mirrored, _s in TEXT_RESIZE:
            if mirrored:
                _select_an(ctx, made[text])
                ctx.com.PositionMirrorHorizontal()
                ctx.wait(0.5)
                calls.append(
                    {
                        "PositionMirrorHorizontal": text,
                        "AN": made[text],
                        "getters": _probe(ctx),
                        "stage": stage,
                    }
                )
        ctx.save()
        return _stage_page(ctx, unit, proj, page, stage, what, src)

    saved = mirror("mirrored", "the texts %s mirrored" % ", ".join(t[0] for t in TEXT_RESIZE if t[3]))
    for text, _x, _y, _m, (sx, sy) in TEXT_RESIZE:
        an = made[text]
        _select_an(ctx, an)
        call = _scale_xy(ctx, _page_record(saved, an), sx, sy, text)
        call.update(AN=an, stage="resized")
        calls.append(call)
    ctx.save()
    _stage_page(
        ctx,
        unit,
        proj,
        page,
        "resized",
        "each text scaled by its x and y factors about its box's top left",
        src,
    )
    mirror("mirrored_back", "the mirrored texts mirrored back")
    ctx.com.PageClose()
    ctx.fresh_builder("the load + save of the resized page, as a later session")
    ctx.open_page(proj, page)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open + save": page, "fresh Graphics Builder": True, "stage": "reopened"})
    _stage_page(ctx, unit, proj, page, "reopened", "its load + save in a fresh Graphics Builder", src)
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "%s/%s" % (proj, page), "calls": calls},
        "The COM calls of the unit in order, with what CtDraw's getters said (the "
        "stages name their folders).",
        src,
    )
    ctx.collect(unit, skip=_page_skip(proj, page))


def _run_outer_mixed(ctx: Oracle, unit: Unit) -> None:
    """Q18: an outer template with two own parameters, one without a
    default and one with. A New Page from it as saved; the outer's load + save and a New
    Page after it; again from its save in a fresh Graphics Builder (a New Page, the outer's
    load + save, a New Page)."""
    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    inner = _template_name(base, "XGA", title)
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    outer_base = made.split("@")[0]
    link = ("%s.%s.<ctt" % (style, made)).encode()
    ctx.open_element(proj, lib, made)
    ans = []
    for text, x, y in OUTER_MIXED:
        before = _objects(ctx)
        ctx.com.DrawText(text, x, y)
        ctx.wait(0.5)
        ans.append(_drawn(ctx, before, "Text", "DrawText(%r)" % text))
    ctx.save()
    ctx.com.PageClose()
    calls.append(
        {"open": made, "DrawText": [list(t) for t in OUTER_MIXED], "ANs": ans, "stage": "params_added"}
    )
    added = _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "params_added",
        [made, inner],
        "DrawText of %s on %s and its save" % (" and ".join(repr(t[0]) for t in OUTER_MIXED), made),
    )
    pages = ["FxOracleMixedNew%d" % k for k in range(1, 5)]
    _new_page4(ctx, unit, style, outer_base, res, title, pages[0], "first/new_page", calls, link, base)
    _follow(ctx, unit, proj, lib, (made,), "resaved", calls)
    _new_page4(ctx, unit, style, outer_base, res, title, pages[1], "resaved/new_page", calls, link, base)
    _put_file(ctx, proj, lib, added)
    ctx.fresh_builder("a New Page from %s and its load + save, as a later session" % made)
    calls.append({"fresh Graphics Builder": True, "library": "as after params_added"})
    _new_page4(ctx, unit, style, outer_base, res, title, pages[2], "fresh/new_page", calls, link, base)
    _follow(ctx, unit, proj, lib, (made,), "fresh", calls)
    _new_page4(
        ctx, unit, style, outer_base, res, title, pages[3], "fresh_resaved/new_page", calls, link, base
    )
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "made": made, "inner": inner, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", *pages))


def _shown_texts(ctx: Oracle, limit: int = SHOWN_ANS) -> dict:
    """What the active window shows of its texts: for each AN 1 .. ``limit`` that
    PageSelectObject selects as itself and AttributeClass calls a Text, its AttributeText
    (that getter only on a text); the ANs that select another object or another class are
    listed with what they selected."""
    texts: Dict[str, str] = {}
    other: Dict[str, list] = {}
    for an in range(1, limit + 1):
        try:
            ctx.com.PageSelectObject(an)
            got = int(ctx.com.AttributeAN())
            cls = str(ctx.com.AttributeClass())
        except Exception:  # noqa: BLE001 (no object with that AN)
            continue
        if got != an or cls != "Text":
            other[str(an)] = [got, cls]
            continue
        try:
            v = ctx.com.AttributeText
            texts[str(an)] = str(v() if callable(v) else v)
        except Exception as e:  # noqa: BLE001
            texts[str(an)] = "error: %s" % (e,)
    with contextlib.suppress(Exception):
        ctx.com.PageSelectObject(0)
    return {"texts": texts, "other": other}


def _copy_placed(ctx: Oracle, unit: Unit, page: str, args: tuple, stage: str, calls: list) -> bytes:
    """``args`` placed on a blank FxMain page (prepare's ``page``) and saved (``<stage>/``),
    then the placement selected and copied (ClipboardCopy). Returns the page."""
    placed = _place_on_blank(ctx, unit, page, args, stage, calls)
    an, rec = _the_genie(placed)
    ctx.open_page("FxMain", page)
    _select_an(ctx, an)
    ctx.com.ClipboardCopy()
    ctx.wait(0.5)
    with contextlib.suppress(Exception):
        ctx.com.PageClose()
    calls.append({"ClipboardCopy": an, "record": rec, "stage": stage})
    return placed


def _paste_into(
    ctx: Oracle, unit: Unit, project: str, lib: str, element: str, stage: str, calls: list, source: str
) -> bytes:
    """ClipboardPaste into ``element``'s window, the texts it shows then read, saved:
    ``<stage>/`` (the library and the element's block). Returns the library."""
    ctx.open_element(project, lib, element)
    before = _objects(ctx)
    ctx.com.ClipboardPaste()
    ctx.wait(1.0)
    after = _objects(ctx)
    shown = _shown_texts(ctx)
    ctx.save()
    ctx.com.PageClose()
    calls.append(
        {
            "open": element,
            "ClipboardPaste": True,
            "objects before": before,
            "objects after": after,
            "shown before the save": shown,
            "stage": stage,
        }
    )
    return _emit_stage(
        ctx,
        unit,
        project,
        lib,
        stage,
        [element],
        "the copy pasted into %s (ClipboardPaste) and %s saved" % (element, element),
        source=source,
    )


def _place_noting(ctx: Oracle, unit: Unit, page: str, args: tuple, stage: str, calls: list) -> bytes:
    """:func:`_place_on_blank`, with the property puts CtDraw refused noted in its call."""
    refused: List[str] = []
    log = ctx.log

    def note(text: str) -> None:
        if text.startswith("LibraryObjectPutProperty("):
            refused.append(text)
        log(text)

    ctx.log = note
    try:
        data = _place_on_blank(ctx, unit, page, args, stage, calls)
    finally:
        del ctx.log
    calls[-1]["property puts refused"] = refused
    return data


def _run_paramless_copy(ctx: Oracle, unit: Unit) -> None:
    """A genie without parameters holding a page-form copy."""
    page = unit.data["page"]
    gp, glib, gel = PAGE_COPY_INTO
    hp, hlib, hel, hx, hy = COPY_HOST
    gsrc, hsrc = "FxLib/%s" % glib, "FxLib/%s" % hlib
    calls: list = []
    _copy_placed(ctx, unit, page, PAGE_COPY, "placed", calls)
    _paste_into(ctx, unit, gp, glib, gel, "pasted", calls, gsrc)
    ctx.open_element(hp, hlib, hel)
    t = _place_in_element(ctx, hp, hlib, hel, gp, os.path.splitext(glib)[0], gel, "ctm", hx, hy)
    ctx.com.PageClose()
    calls.append(
        {
            "open": hel,
            "LibraryObjectPlaceEx": [gp, os.path.splitext(glib)[0], gel, t, False, hx, hy],
            "stage": "host_made",
        }
    )
    host = _emit_stage(
        ctx,
        unit,
        hp,
        hlib,
        "host_made",
        [hel],
        "%s placed into %s and %s saved" % (gel, hel, hel),
        source=hsrc,
    )
    text, x, y = COPY_GAINED
    ctx.open_element(gp, glib, gel)
    before = _objects(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": gel, "DrawText": [text, x, y], "AN": an, "stage": "gained"})
    gained = _emit_stage(
        ctx, unit, gp, glib, "gained", [gel], "DrawText of %r in %s and its save" % (text, gel), source=gsrc
    )
    _follow(ctx, unit, hp, hlib, (hel,), "same", calls)
    _place_noting(ctx, unit, "FxOracleDraw", COPY_HOST_PLACE, "same/page", calls)
    _put_file(ctx, hp, hlib, host)
    _put_file(ctx, gp, glib, gained)
    ctx.fresh_builder("the load + save of %s after %s gained a parameter, as a later session" % (hel, gel))
    calls.append(
        {
            "fresh Graphics Builder": True,
            "libraries": "%s as after host_made, %s as after gained" % (hlib, glib),
        }
    )
    _follow(ctx, unit, hp, hlib, (hel,), "fresh", calls)
    _place_noting(ctx, unit, "FxOracleDraw", COPY_HOST_PLACE, "fresh/page", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"pasted into": "%s/%s/%s" % (gp, glib, gel), "host": "%s/%s/%s" % (hp, hlib, hel), "calls": calls},
        "The COM calls of the unit in order, with the texts the window showed (the "
        "stages name their folders).",
    )
    ctx.collect(
        unit, skip={"%s/%s" % (gp, glib), "%s/%s" % (hp, hlib)} | _page_skip("FxMain", page, "FxOracleDraw")
    )


def _run_stale_page(ctx: Oracle, unit: Unit) -> None:
    """Q20: a page that places a host that is still stale. n2 placed on
    a blank page; n0 gains a text and is saved, n1 is opened and saved, n2 is not; the page's
    load + save in the same Graphics Builder and, from the page as placed, in a fresh one;
    a new placement of n2 there."""
    proj, lib = "FxLib", "fxnest.ctm"
    page = unit.data["page"]
    calls: list = []
    placed = _place_on_blank(ctx, unit, page, STALE_PLACE, "placed", calls)
    text, x, y = CHAIN_GAINED
    ctx.open_element(proj, lib, CHAIN_EDITED)
    before = _objects(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": CHAIN_EDITED, "DrawText": [text, x, y], "AN": an, "stage": "n0_gained"})
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "n0_gained",
        [CHAIN_EDITED],
        "CtDraw's DrawText of %r in %s and its save" % (text, CHAIN_EDITED),
    )
    _follow(ctx, unit, proj, lib, ("n1",), "n1_saved", calls)
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "same",
        calls,
        "its load + save after n0 and n1 were saved (n2 not), in the same Graphics Builder",
    )
    _put_file(ctx, "FxMain", page + ".ctg", placed)
    ctx.fresh_builder("the page's load + save over the stale n2, as a later session")
    calls.append({"fresh Graphics Builder": True, "page": "as placed", "library": "as after n1_saved"})
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "fresh",
        calls,
        "its load + save after n0 and n1 were saved (n2 not), in a fresh Graphics Builder",
    )
    _place_on_blank(ctx, unit, "FxOracleDraw", STALE_PLACE, "fresh/placed", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "FxMain/%s" % page, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", page, "FxOracleDraw"))


def _run_template_placeholder(ctx: Oracle, unit: Unit) -> None:
    """A page linked to a template that holds a placeholder."""
    from ctgedit.format.element import LibraryFile

    tp, tlib, tel = HOLDER_TEMPLATE
    mp, mlib = HOLDER_MISSING
    style = os.path.splitext(tlib)[0]
    res, title = OUTER_BASE[1], OUTER_BASE[2]
    tsrc, psrc = unit.sources[0], unit.sources[2]
    calls: list = []
    path = _find_file(os.path.join(ctx.inst.user, mp), mlib)
    if path is None:
        raise UnitFailed("%s has no %s" % (mp, mlib))
    keep = _read(path)
    lib0 = ctx.read(tp, tlib)
    page = HOLDER_PAGE
    page0 = ctx.read("FxMain", page + ".ctg")
    if page0 is None or lib0 is None:
        raise UnitFailed("FxMain has no %s or %s no %s" % (page, tp, tlib))

    def holds(data: Optional[bytes], el: str) -> bool:
        lf = LibraryFile(data or b"")
        slot = lf.find(el)
        return slot is not None and b"_library" in lf.block_data(slot).lower()

    def actions() -> None:
        ctx.emit_json(
            unit,
            "actions.json",
            {"page": "FxMain/%s" % page, "template": "%s/%s/%s" % (tp, tlib, template), "calls": calls},
            "The COM calls of the unit in order (the stages name their folders).",
            tsrc,
        )

    os.remove(path)
    calls.append({"removed": "%s/%s" % (mp, os.path.basename(path))})
    template: Optional[str] = None
    for k, stage in enumerate(("template_missing", "template_missing_fresh")):
        if k:
            _put_file(ctx, tp, tlib, lib0)
            ctx.fresh_builder("%s's open + save in a builder started without %s" % (tel, mlib), force=True)
        ctx.open_element(tp, tlib, tel)
        ctx.save()
        ctx.com.PageClose()
        data = _emit_stage(
            ctx, unit, tp, tlib, stage, [tel], "%s's open + save with %s missing" % (tel, mlib), source=tsrc
        )
        has = holds(data, tel)
        calls.append(
            {
                "open + save": tel,
                "library": "missing",
                "placeholder": has,
                "fresh Graphics Builder": bool(k),
                "stage": stage,
            }
        )
        if has:
            template = tel
            break
    if template is None:
        # a new template from it, unlinked: CtDraw composes the copied genie afresh
        names0 = set(LibraryFile(ctx.read(tp, tlib) or b"").element_names())
        ctx.project(tp)
        ctx.used = True
        with contextlib.suppress(Exception):
            ctx.com.PageClose()
        mark = _dialog_mark(ctx)
        ctx.com.PageNewTemplate(tp, style, tel.split("@")[0], res, title, False)
        ctx.wait(1.0)
        rec: dict = {
            "PageNewTemplate": [tp, style, tel.split("@")[0], res, title, False],
            "PageName": ctx.page_name(),
        }
        made = None
        for name in (HOLDER_NAME, _template_name(HOLDER_NAME, "XGA", title)):
            try:
                ctx.com.PageSaveAsEx(tp, style, name)
            except Exception as e:  # noqa: BLE001
                rec.setdefault("PageSaveAsEx errors", []).append([name, str(e)])
                continue
            ctx.wait(1.0)
            new = [n for n in LibraryFile(ctx.read(tp, tlib) or b"").element_names() if n not in names0]
            if new:
                made = new[0]
                break
        with contextlib.suppress(Exception):
            ctx.com.PageClose()
        rec.update(_dialogs_since(ctx, mark))
        if made is None:
            calls.append(rec)
            actions()
            raise UnitFailed("PageNewTemplate + PageSaveAsEx made no template in %s" % tlib)
        ctx.opened.add((style.lower(), made.lower()))
        data = _emit_stage(
            ctx,
            unit,
            tp,
            tlib,
            "holder_made",
            [made],
            "PageNewTemplate from %s, unlinked, with %s missing, saved as %s" % (tel, mlib, made),
            source=tsrc,
        )
        rec.update({"made": made, "placeholder": holds(data, made), "stage": "holder_made"})
        calls.append(rec)
        if rec["placeholder"]:
            template = made
            got = _new_page_from(
                ctx,
                unit,
                style,
                made.split("@")[0],
                res,
                title,
                HOLDER_LINKED_PAGE,
                "page_made",
                calls,
                want=("%s.%s.<ctt" % (style, made)).encode(),
            )
            page, page0 = HOLDER_LINKED_PAGE, got
    _write(path, keep)
    calls.append({"put back": "%s/%s" % (mp, os.path.basename(path))})
    if template is None:
        actions()
        raise UnitFailed(
            "no template holds a placeholder: %s's open + save with %s missing "
            "(twice) and a new template from it wrote none" % (tel, mlib)
        )
    ctx.fresh_builder("the page's load + save with %s back" % mlib)
    calls.append({"fresh Graphics Builder": True, "library": "back"})
    source = psrc if page == HOLDER_PAGE else None

    def load_save(stage: str, what: str) -> bytes:
        ctx.open_page("FxMain", page)
        ctx.save()
        ctx.com.PageClose()
        calls.append({"open + save": page, "stage": stage})
        return _stage_page(ctx, unit, "FxMain", page, stage, what, source)

    load_save(
        "page_back",
        "its load + save with %s back, the template %s holding the placeholder" % (mlib, template),
    )
    _new_page_from(
        ctx,
        unit,
        style,
        template.split("@")[0],
        res,
        title,
        HOLDER_NEW_PAGE,
        "new_page",
        calls,
        want=("%s.%s.<ctt" % (style, template)).encode(),
    )
    _follow(ctx, unit, tp, tlib, (template,), "template_back", calls)
    _put_file(ctx, "FxMain", page + ".ctg", page0)
    load_save(
        "page_after",
        "its load + save (from the page as it was) after %s's load + save with %s back" % (template, mlib),
    )
    _put_file(ctx, "FxMain", page + ".ctg", page0)
    ctx.fresh_builder("the page's load + save after the template's, as a later session")
    calls.append({"fresh Graphics Builder": True, "page": "as it was", "library": "as after template_back"})
    load_save(
        "page_after_fresh",
        "its load + save (from the page as it was) after the "
        "template's load + save, in a fresh Graphics Builder",
    )
    actions()
    ctx.collect(
        unit,
        skip={"%s/%s" % (tp, tlib), "%s/%s" % (mp, mlib)}
        | _page_skip("FxMain", HOLDER_PAGE, HOLDER_LINKED_PAGE, HOLDER_NEW_PAGE),
    )


def _run_pasted_copy(ctx: Oracle, unit: Unit) -> None:
    """Q22: a genie copy pasted into a genie element window. n2 placed
    on a blank page with Name 'P' and copied, pasted into n3's window (the texts it shows
    read), n3 saved; n3's load + save in a fresh Graphics Builder, the texts read again."""
    page = unit.data["page"]
    proj, lib, el = PASTE_INTO
    calls: list = []
    _copy_placed(ctx, unit, page, PASTE_PLACE, "placed", calls)
    pasted = _paste_into(ctx, unit, proj, lib, el, "pasted", calls, unit.sources[0])
    _put_file(ctx, proj, lib, pasted)
    ctx.fresh_builder("the load + save of %s with the pasted copy, as a later session" % el)
    ctx.open_element(proj, lib, el)
    shown = _shown_texts(ctx)
    ctx.save()
    ctx.com.PageClose()
    calls.append(
        {"open + save": el, "shown": shown, "fresh Graphics Builder": True, "stage": "reopened/%s" % el}
    )
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "reopened/%s" % el,
        [el],
        "CtDraw's open + save of %s in a fresh Graphics Builder" % el,
    )
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order, with the texts the window showed (the "
        "stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", page))


# ---------------------------------------------------------------------------- the chain7 step
def _oracle_file(*parts: str) -> bytes:
    """A file of the committed oracle (an earlier step's capture of fixture content), the
    input of a unit."""
    path = os.path.join(ORACLE, *[q for p in parts for q in p.split("/")])
    data = _read(path)
    if data is None:
        raise UnitFailed("the oracle has no %s: capture that unit first" % "/".join(parts))
    return data


def _put_file7(ctx: Oracle, project: str, name: str, data: bytes, tries: int = 20) -> None:
    """:func:`_put_file`, tried again while another process holds the file (Studio's start
    reads the libraries of its project: a replace right after it can be refused)."""
    for k in range(tries):
        try:
            _put_file(ctx, project, name, data)
            return
        except PermissionError as e:
            if k + 1 == tries:
                raise UnitFailed("%s\\%s stays held by another process: %s" % (project, name, e)) from e
            ctx.log("%s\\%s is held by another process; trying again." % (project, name))
            time.sleep(1.0)


def _screen_dpi() -> dict:
    """The screen's LOGPIXELSX / LOGPIXELSY (the DPI CtDraw measures its texts at)."""
    try:
        import ctypes

        dc = ctypes.windll.user32.GetDC(0)
        try:
            return {
                "LOGPIXELSX": ctypes.windll.gdi32.GetDeviceCaps(dc, 88),
                "LOGPIXELSY": ctypes.windll.gdi32.GetDeviceCaps(dc, 90),
            }
        finally:
            ctypes.windll.user32.ReleaseDC(0, dc)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _new_page_unlinked(
    ctx: Oracle,
    unit: Unit,
    style: str,
    template: str,
    res: int,
    title: bool,
    page: str,
    stage: str,
    calls: list,
) -> bytes:
    """CtDraw's New Page (PageNew, not linked) from ``style`` ``template``, saved into FxMain
    as ``page``: ``<stage>/page.ctg`` and its .ctF. Tried from FxMain, then from the
    template's own project."""
    ctx.project("FxMain")
    ctx.used = True
    attempts = []
    data = None
    for args in (
        ("FxMain", style, template, res, title, False),
        (OUTER_PROJECT, style, template, res, title, False),
    ):
        data, what = _page_new(ctx, args, page)
        attempts.append({"PageNew": list(args), "PageSaveAs": ["FxMain", page], "result": what})
        if data is not None:
            break
    calls.append({"New Page (unlinked)": attempts, "stage": stage})
    if data is None:
        raise UnitFailed("the unlinked PageNew from %s %s: %s" % (style, template, attempts))
    ctx.emit(
        unit,
        "%s/page.ctg" % stage,
        data,
        "%s.ctg: CtDraw's unlinked New Page from %s %s (%s)." % (page, style, template, stage),
    )
    ctf = ctx.read("FxMain", page + ".ctF")
    if ctf is not None:
        ctx.emit(unit, "%s/page.ctF" % stage, ctf, "The .ctF CtDraw wrote with it.")
    return data


def _run_holder_pages(ctx: Oracle, unit: Unit) -> None:
    """Pages made and cut from a template holding a placeholder."""
    from ctgedit.format.element import LibraryFile

    tp, tlib, tel = HOLDER_TEMPLATE
    style = os.path.splitext(tlib)[0]
    res, title = OUTER_BASE[1], OUTER_BASE[2]
    calls: list = []
    held = _oracle_file(*HOLDER_CAPTURE)
    lf = LibraryFile(held)
    if b"_library" not in lf.block_data(lf.find(tel)).lower():
        raise UnitFailed("%s holds no placeholder" % "/".join(HOLDER_CAPTURE))
    link = ("%s.%s.<ctt" % (style, tel)).encode()

    def put_template(why: str) -> None:
        if ctx.read(tp, tlib) != held:
            _put_file7(ctx, tp, tlib, held)
            calls.append(
                {
                    "library": "%s/%s as %s" % (tp, tlib, "/".join(HOLDER_CAPTURE)),
                    "sha1": sha1(held),
                    "why": why,
                }
            )

    try:
        put_template("the template holding the placeholder")
        linked = _new_page_from(
            ctx, unit, style, tel.split("@")[0], res, title, HOLDER7_LINKED, "linked", calls, want=link
        )
        put_template("as it was before the linked New Page")
        ctx.fresh_builder("an unlinked New Page from the template holding the placeholder")
        calls.append({"fresh Graphics Builder": True})
        _new_page_unlinked(
            ctx, unit, style, tel.split("@")[0], res, title, HOLDER7_UNLINKED, "unlinked", calls
        )
        put_template("as it was before the unlinked New Page")
        ctx.fresh_builder("Cut Link of %s, linked to the template holding the placeholder" % HOLDER_PAGE)
        calls.append({"fresh Graphics Builder": True})
        _cut_link4(ctx, unit, "FxMain", HOLDER_PAGE, "cut_fixture", calls)
        put_template("as it was before the Cut Link")
        _put_file7(ctx, "FxMain", HOLDER7_LINKED + ".ctg", linked)
        ctx.fresh_builder("Cut Link of the linked New Page %s" % HOLDER7_LINKED)
        calls.append({"fresh Graphics Builder": True, "page": "%s as made" % HOLDER7_LINKED})
        _cut_link4(ctx, unit, "FxMain", HOLDER7_LINKED, "cut_linked", calls)
    finally:
        ctx.emit_json(
            unit,
            "actions.json",
            {"template": "%s/%s/%s" % (tp, tlib, tel), "input": "/".join(HOLDER_CAPTURE), "calls": calls},
            "The COM calls of the unit in order (the stages name their folders).",
            unit.sources[0],
        )
    ctx.collect(
        unit,
        skip={"%s/%s" % (tp, tlib)} | _page_skip("FxMain", HOLDER_PAGE, HOLDER7_LINKED, HOLDER7_UNLINKED),
    )


def _run_paste_paramless(ctx: Oracle, unit: Unit) -> None:
    """Q25 (a): a page copy of a genie without parameters pasted into a
    genie element window. fx_plain placed on a blank page and copied, pasted into n0's window
    (ClipboardPaste, not LibraryObjectPlaceEx), n0 saved; n1's load + save; n0's and n1's
    in a fresh Graphics Builder."""
    page = unit.data["page"]
    proj, lib, el = PASTE7_INTO
    calls: list = []
    _copy_placed(ctx, unit, page, PASTE7_PLACE, "placed", calls)
    pasted = _paste_into(ctx, unit, proj, lib, el, "pasted", calls, "FxLib/%s" % lib)
    _follow(ctx, unit, proj, lib, ("n1",), "same", calls)
    _put_file7(ctx, proj, lib, pasted)
    ctx.fresh_builder("the load + save of %s and n1 after the paste, as a later session" % el)
    calls.append({"fresh Graphics Builder": True, "library": "as after pasted"})
    _follow(ctx, unit, proj, lib, (el, "n1"), "fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order, with the texts the window showed (the "
        "stages name their folders).",
        "FxLib/%s" % lib,
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", page))


def _draw_outer_param(ctx: Oracle, unit: Unit, made: str, calls: list) -> bytes:
    """OUTER_PARAM drawn on the outer template ``made`` and saved: ``param_added/``."""
    proj, lib = OUTER_PROJECT, OUTER_STYLE + ".ctt"
    text, x, y = OUTER_PARAM
    ctx.open_element(proj, lib, made)
    before = _objects(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    ctx.save()
    ctx.com.PageClose()
    calls.append({"open": made, "DrawText": [text, x, y], "AN": an, "stage": "param_added"})
    return _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "param_added",
        [made],
        "DrawText of %r on %s and its save (its own parameter)" % (text, made),
    )


def _run_outer_param_page(ctx: Oracle, unit: Unit) -> None:
    """A template update on a page made from an outer template."""
    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    page = unit.data["page"]
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    link = ("%s.%s.<ctt" % (style, made)).encode()
    _draw_outer_param(ctx, unit, made, calls)
    made_page = _new_page4(
        ctx, unit, style, made.split("@")[0], res, title, page, "page_made", calls, link, None
    )
    changed = _change_inner(ctx, unit, calls, made)
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "same",
        calls,
        "its load + save after %s changed, in the same Graphics Builder" % OUTER_BASE[0],
    )
    _put_file7(ctx, "FxMain", page + ".ctg", made_page)
    _put_file7(ctx, proj, lib, changed)
    ctx.fresh_builder("the page's load + save after %s changed, as a later session" % OUTER_BASE[0])
    calls.append({"fresh Graphics Builder": True, "page": "as made", "library": "as after inner_changed"})
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "fresh",
        calls,
        "its load + save after %s changed, in a fresh Graphics Builder" % OUTER_BASE[0],
    )
    _follow(ctx, unit, proj, lib, (made,), "fresh/outer", calls)
    _put_file7(ctx, "FxMain", page + ".ctg", made_page)
    _load_save_page(
        ctx,
        unit,
        "FxMain",
        page,
        "fresh/after_outer",
        calls,
        "its load + save (from the page as made) after the outer's load + save",
    )
    _put_file7(ctx, "FxMain", page + ".ctg", made_page)
    _put_file7(ctx, proj, lib, changed)
    ctx.fresh_builder("Tools > Update Pages after %s changed, as a later session" % OUTER_BASE[0])
    calls.append({"fresh Graphics Builder": True, "page": "as made", "library": "as after inner_changed"})
    ctx.project("FxMain")
    ctx.used = True
    mark = _dialog_mark(ctx)
    t0 = time.time()
    with ctx.answer("Yes"):
        try:
            r = ctx.com.ProjectUpdatePages(True)
            result = "returned %r" % (r,)
        except Exception as e:  # noqa: BLE001
            result = "error: %s" % (e,)
        waited = _wait_quiet(ctx, quiet=2.0)
    calls.append(
        {
            "ProjectUpdatePages": [True],
            "project": "FxMain",
            "result": result,
            "seconds": round(time.time() - t0, 1),
            "waited": waited,
            "watch": _dialogs_since(ctx, mark),
            "stage": "update_pages",
        }
    )
    _stage_page(
        ctx,
        unit,
        "FxMain",
        page,
        "update_pages",
        "Tools > Update Pages over COM (ProjectUpdatePages True) on FxMain",
    )
    _emit_stage(
        ctx, unit, proj, lib, "update_pages", [made], "Tools > Update Pages over COM on FxMain", library=False
    )
    files = sorted(rel for rel, _b, _a in ctx.changes())
    ctx.emit_json(
        unit,
        "actions.json",
        {
            "library": "%s/%s" % (proj, lib),
            "made": made,
            "page": "FxMain/%s" % page,
            "calls": calls,
            "files changed": files,
        },
        "The COM calls of the unit in order (the stages name their folders) and "
        "every file the unit changed (not collected: an Update Pages run writes "
        "many pages and their rows).",
        unit.sources[0],
    )


def _place_in_window(
    ctx: Oracle,
    project: str,
    lib: str,
    element: str,
    lib_project: str,
    plib: str,
    pel: str,
    ext: str,
    x: int,
    y: int,
    props,
) -> dict:
    """LibraryObjectPlaceEx into the open element window (its type tried 0 .. 4, cached per
    extension) with LibraryObjectPutProperty of each of ``props`` after it, saved; the
    element's block must then name ``plib.pel.<ext``. The type and the puts refused."""
    from ctgedit.format.element import LibraryFile

    key = "place type " + ext
    order = list(range(5))
    if key in ctx.cache:
        order.remove(ctx.cache[key])
        order.insert(0, ctx.cache[key])
    want = ("%s.%s.<%s" % (plib, pel, ext)).lower().encode()
    for t in order:
        refused = []
        try:
            ctx.com.LibraryObjectPlaceEx(lib_project, plib, pel, t, ext == "ctl", x, y)
        except Exception:  # noqa: BLE001
            continue
        for k, v in props:
            try:
                ctx.com.LibraryObjectPutProperty(k, v)
            except Exception as e:  # noqa: BLE001
                refused.append("LibraryObjectPutProperty(%s, %r): %s" % (k, v, e))
        ctx.wait(0.5)
        ctx.save()
        lf = LibraryFile(ctx.read(project, lib) or b"")
        slot = lf.find(element)
        if slot is not None and want in lf.block_data(slot).lower():
            ctx.cache[key] = t
            return {"type": t, "puts refused": refused}
    raise UnitFailed("LibraryObjectPlaceEx placed no %s.%s in %s" % (plib, pel, element))


def _run_genie_in_outer(ctx: Oracle, unit: Unit) -> None:
    """Q25 (c): genies placed into a template that already has a
    parameter of its own. fxouter made, '%P%' drawn on it, saved; n0 (with Label put) and
    fx_plain placed into its window, saved; its load + save in a fresh Graphics Builder and a
    New Page from it."""
    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    link = ("%s.%s.<ctt" % (style, made)).encode()
    _draw_outer_param(ctx, unit, made, calls)
    ctx.open_element(proj, lib, made)
    for lp, plib, pel, x, y, props in OUTER_GENIES:
        got = _place_in_window(ctx, proj, lib, made, lp, plib, pel, "ctm", x, y, props)
        calls.append(
            {
                "open": made,
                "LibraryObjectPlaceEx": [lp, plib, pel, got["type"], False, x, y],
                "puts": [list(p) for p in props],
                "puts refused": got["puts refused"],
                "stage": "placed",
            }
        )
    ctx.com.PageClose()
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "placed",
        [made],
        "%s placed into %s and %s saved"
        % (" and ".join("%s.%s" % (g[1], g[2]) for g in OUTER_GENIES), made, made),
    )
    ctx.fresh_builder("the load + save of %s with the genies, as a later session" % made)
    calls.append({"fresh Graphics Builder": True, "library": "as after placed"})
    _follow(ctx, unit, proj, lib, (made,), "fresh", calls)
    _new_page4(
        ctx,
        unit,
        style,
        made.split("@")[0],
        res,
        title,
        OUTER_GENIE_PAGE,
        "fresh/new_page",
        calls,
        link,
        None,
    )
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "made": made, "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", OUTER_GENIE_PAGE))


def _run_label_blank(ctx: Oracle, unit: Unit) -> None:
    """Q25 (d): leading blanks. n0's text set to ' %Label%', n0 saved,
    n1's load + save; n0 placed into n1 with Label ' %Name% L1', n1 saved; a placement of n1
    with Name 'P1'; n1's load + save in a fresh Graphics Builder."""
    proj, lib = unit.data["project"], "fxnest.ctm"
    key = "%s/%s/%s" % (proj, lib, CHAIN_EDITED)
    calls: list = []
    ctx.open_element(proj, lib, CHAIN_EDITED)
    an = _select_text(ctx, key)
    call = _put_text(ctx, an, BLANK_TEXT)
    ctx.save()
    ctx.com.PageClose()
    call.update(open=CHAIN_EDITED, stage="n0_blank")
    calls.append(call)
    _emit_stage(
        ctx,
        unit,
        proj,
        lib,
        "n0_blank",
        [CHAIN_EDITED],
        "%s's text set to %r and %s saved" % (CHAIN_EDITED, BLANK_TEXT, CHAIN_EDITED),
    )
    _follow(ctx, unit, proj, lib, ("n1",), "n1_after", calls)
    lp, plib, pel, x, y, props = BLANK_COPY
    ctx.open_element(proj, lib, "n1")
    got = _place_in_window(ctx, proj, lib, "n1", lp, plib, pel, "ctm", x, y, props)
    ctx.com.PageClose()
    calls.append(
        {
            "open": "n1",
            "LibraryObjectPlaceEx": [lp, plib, pel, got["type"], False, x, y],
            "puts": [list(p) for p in props],
            "puts refused": got["puts refused"],
            "stage": "n1_bound",
        }
    )
    bound = _emit_stage(
        ctx, unit, proj, lib, "n1_bound", ["n1"], "%s placed into n1 with %r and n1 saved" % (pel, props)
    )
    _place_noting(ctx, unit, unit.data["page"], BLANK_PLACE, "page", calls)
    _put_file7(ctx, proj, lib, bound)
    ctx.fresh_builder("n1's load + save with the bound copy, as a later session")
    calls.append({"fresh Graphics Builder": True, "library": "as after n1_bound"})
    _follow(ctx, unit, proj, lib, ("n1",), "fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", unit.data["page"]))


def _window_objects(ctx: Oracle) -> dict:
    """What the active window holds: (AN, class) through PageAllGetFirstAN / NextAN, and the
    template's objects through PageTemplateSelectFirstObject / NextObject (each list ends at
    the error that ends it or at an AN met again; AttributeClass, never AttributeText*)."""
    out: dict = {}
    for key, first, nxt in (
        ("all", "PageAllGetFirstAN", "PageAllGetNextAN"),
        ("template", "PageTemplateSelectFirstObject", "PageTemplateSelectNextObject"),
    ):
        items: list = []
        try:
            getattr(ctx.com, first)()
        except Exception as e:  # noqa: BLE001
            out[key] = "error: %s" % (e,)
            continue
        seen = set()
        for _n in range(500):
            try:
                an = int(ctx.com.AttributeAN())
                cls = str(ctx.com.AttributeClass())
            except Exception:  # noqa: BLE001
                break
            if an in seen:
                break
            seen.add(an)
            items.append([an, cls])
            try:
                getattr(ctx.com, nxt)()
            except Exception:  # noqa: BLE001 (the last one)
                break
        out[key] = items
    with contextlib.suppress(Exception):
        ctx.com.PageSelectObject(0)
    return out


def _open_window(
    ctx: Oracle,
    project: str,
    page: Optional[str] = None,
    lib: Optional[str] = None,
    element: Optional[str] = None,
    select: bool = True,
) -> str:
    """A page (PageOpen) or a template (PageOpenEx, TEMPLATE_PAGE_TYPE) opened in a window of
    its own, the windows open before left open (``select`` False: no ProjectSelect first, so
    a change of project cannot close them). Returns its PageName."""
    if select:
        ctx.project(project)
    ctx.used = True
    if lib:
        stem = os.path.splitext(lib)[0]
        ctx.opened.add((stem.lower(), element.lower()))
        ctx.com.PageOpenEx(project, stem, element, TEMPLATE_PAGE_TYPE)
        want = element.split("@")[0]
    else:
        ctx.com.PageOpen(project, page)
        want = page
    ctx.wait(1.0)
    got = ctx.page_name()
    if want.lower() not in got.lower():
        raise UnitFailed("opening %s left %r active" % (element or page, got))
    return got


def _activate(ctx: Oracle, project: str, page: str) -> list:
    """Make the open window of ``page`` the active one: PageSelect (two argument forms),
    else PageSelectFirst / PageSelectNext until PageName names it. Returns the tries."""
    tries: list = []
    for args in ((project, page), (page, "")):
        try:
            ctx.com.PageSelect(*args)
        except Exception as e:  # noqa: BLE001
            tries.append([list(args), "error: %s" % (e,)])
            continue
        got = ctx.page_name()
        tries.append([list(args), got])
        if page.lower() in got.lower():
            return tries
    try:
        ctx.com.PageSelectFirst()
        for _n in range(10):
            got = ctx.page_name()
            tries.append(["PageSelectFirst / Next", got])
            if page.lower() in got.lower():
                return tries
            ctx.com.PageSelectNext()
    except Exception as e:  # noqa: BLE001
        tries.append(["PageSelectFirst / Next", "error: %s" % (e,)])
    raise UnitFailed("the window of %s could not be made active: %s" % (page, tries))


def _close_windows(ctx: Oracle, limit: int = 10) -> List[str]:
    """PageClose until no window is open (a question to save is answered No): the PageNames
    closed."""
    closed: List[str] = []
    for _n in range(limit):
        name = ctx.page_name()
        if not name:
            break
        try:
            ctx.com.PageClose()
        except Exception:  # noqa: BLE001
            break
        closed.append(name)
        ctx.wait(0.5)
    return closed


def _run_open_pages(ctx: Oracle, unit: Unit) -> None:
    """Pages open in CtDraw while their template is saved."""
    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    inner = _template_name(base, "XGA", title)
    calls: list = []
    made = _make_outer(ctx, unit, calls)
    link = ("%s.%s.<ctt" % (style, made)).encode()
    _new_page4(
        ctx, unit, style, made.split("@")[0], res, title, OUTER_LINKED_PAGE, "page_made", calls, link, None
    )
    lib0 = ctx.read(proj, lib)
    pages0 = {p: ctx.read("FxMain", p + ".ctg") for p in OPEN_PAGES}
    try:
        for k, how in enumerate(("menu", "com")):
            if k:
                _put_file7(ctx, proj, lib, lib0)
                for p in OPEN_PAGES:
                    _put_file7(ctx, "FxMain", p + ".ctg", pages0[p])
            ctx.fresh_builder(
                "the pages opened in their windows, then %s saved (%s)" % (inner, how),
                force=ctx.builder_gone(),
            )
            rec: dict = {"how": how, "fresh Graphics Builder": True, "stage": how}
            shown0 = {}
            for p in OPEN_PAGES:
                rec.setdefault("opened", []).append(_open_window(ctx, "FxMain", page=p))
                shown0[p] = _window_objects(ctx)
            rec["opened"].append(_open_window(ctx, proj, lib=lib, element=inner, select=False))
            rec["still open"] = []
            for p in OPEN_PAGES:
                rec["still open"].append(_activate(ctx, "FxMain", p))
            _activate(ctx, proj, inner)
            before = _objects(ctx)
            ctx.com.DrawRectangle(*INNER_RECT)
            ctx.wait(0.5)
            rec["rectangle AN"] = _drawn(ctx, before, "SquareV3", "DrawRectangle")
            with contextlib.suppress(Exception):
                ctx.com.PageSelectObject(0)
            disk0 = {p: ctx.read("FxMain", p + ".ctg") for p in OPEN_PAGES}
            mark = _dialog_mark(ctx)
            with ctx.answer("Yes"):
                if how == "menu":
                    ctx.menu_command(MENU_SAVE)
                    rec["saved with"] = "File > Save (menu %d)" % MENU_SAVE
                else:
                    ctx.com.PageSave()
                    rec["saved with"] = "PageSave"
                rec["waited"] = _wait_quiet(ctx)
                rec["PageName after"] = ctx.page_name()
            rec["watch"] = _dialogs_since(ctx, mark)
            rec["template saved"] = ctx.read(proj, lib) != lib0
            _emit_stage(
                ctx,
                unit,
                proj,
                lib,
                "%s/template" % how,
                [inner, made],
                "a "
                "rectangle drawn on %s and saved (%s) while %s were open"
                % (inner, rec["saved with"], " and ".join(OPEN_PAGES)),
            )
            if not rec["template saved"]:
                calls.append(rec)
                raise UnitFailed("%s was not saved (%s)" % (inner, how))
            windows = {}
            for p in OPEN_PAGES:
                w: dict = {"shown at its open": shown0[p]}
                disk = ctx.read("FxMain", p + ".ctg")
                w["file changed by the template's save"] = disk != disk0[p]
                if disk != disk0[p] and disk is not None:
                    ctx.emit(
                        unit,
                        "%s/%s/on_disk.ctg" % (how, p),
                        disk,
                        "%s.ctg as the template's save left it on disk (the page still open)." % p,
                    )
                w["activated"] = _activate(ctx, "FxMain", p)
                w["shown after the template's save"] = _window_objects(ctx)
                png = ctx.shot()
                if png:
                    ctx.emit(
                        unit,
                        "%s/%s/canvas.png" % (how, p),
                        png,
                        "CtDraw's canvas of %s after the template's save, before its own." % p,
                    )
                mark = _dialog_mark(ctx)
                with ctx.answer("Yes"):
                    ctx.save()
                w["its save"] = _dialogs_since(ctx, mark)
                _stage_page(
                    ctx,
                    unit,
                    "FxMain",
                    p,
                    "%s/%s" % (how, p),
                    "its save in its window after %s's save (%s)" % (inner, rec["saved with"]),
                )
                windows[p] = w
            rec["windows"] = windows
            rec["closed"] = _close_windows(ctx)
            calls.append(rec)
    finally:
        ctx.emit_json(
            unit,
            "actions.json",
            {"library": "%s/%s" % (proj, lib), "made": made, "pages": list(OPEN_PAGES), "calls": calls},
            "The COM calls of the unit in order, with what each window showed (the "
            "stages name their folders).",
            unit.sources[0],
        )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", *OPEN_PAGES))


def _marker_library(data: bytes, lib: str, template: bytes) -> Tuple[bytes, List[dict]]:
    """``lib`` (its bytes ``data``) with the MARKER_SYMBOLS elements written in: each holds
    one record, MARKER_FROM's free DummyMarker given its handle 0, its box and slots, under
    the head and trailer of the library's first element and a new identity."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.element import LibraryFile, element_content, open_element, refresh_identity

    _tp, tlib, tel = MARKER_FROM
    pt = open_element(tlib, tel, library=LibraryFile(template))
    marks = [po for po in pt.tree if po.type_code == 0xFFFF]
    if len(marks) != 1:
        raise UnitFailed("%s holds %d DummyMarkers, not one" % (tel, len(marks)))
    lf = LibraryFile(data)
    base = open_element(lib, lf.element_names()[0], library=lf)
    made = []
    for name, anchor, rect, hotspot, slots in MARKER_SYMBOLS:
        if lf.find(name) is not None:
            raise UnitFailed("%s already has %s" % (lib, name))
        r = GP.decode(marks[0])
        r.handle = 0
        r.anchor, r.rect1, r.rect2 = tuple(anchor), tuple(rect), tuple(rect)
        if hotspot is not None:
            if len(r.cls) != 12:
                raise UnitFailed("%s's DummyMarker has a class part of %d bytes, not 12" % (tel, len(r.cls)))
            r.cls = struct.pack("<4h2h", *(tuple(hotspot[0]) + tuple(hotspot[1])))
        if slots is not None:
            r.slots = [(m, tuple(x)) for m, x in slots]
        po = GP.to_po(GP.encode(r))
        lf.write_element(name, refresh_identity(element_content(base, objects=[po])))
        made.append(
            {
                "element": name,
                "anchor": list(anchor),
                "rect": list(rect),
                "class part": list(struct.unpack("<4h2h", r.cls)),
                "slots": [[m, list(x)] for m, x in r.slots],
                "head of": base.name,
            }
        )
    return lf.to_bytes(), made


def _run_marker_symbol(ctx: Oracle, unit: Unit) -> None:
    """Q28 (b): a symbol whose only record is a DummyMarker. The tool
    writes them into fxsym.ctl; CtDraw's open + save of each (one record, or a root group
    around it), and again in a fresh Graphics Builder."""
    proj, lib = MARKER_LIB
    calls: list = []
    data, made = _marker_library(
        ctx.read(proj, lib) or b"", lib, ctx.read(MARKER_FROM[0], MARKER_FROM[1]) or b""
    )
    _put_file7(ctx, proj, lib, data)
    names = [m[0] for m in MARKER_SYMBOLS]
    calls.append({"made": made, "stage": "input"})
    _emit_stage(
        ctx, unit, proj, lib, "input", names, "the symbols of one DummyMarker the tool wrote into %s" % lib
    )
    for el in names:
        mark = _dialog_mark(ctx)
        ptype = ctx.open_element(proj, lib, el)
        shown = _objects(ctx)
        ctx.save()
        ctx.com.PageClose()
        calls.append(
            {
                "open + save": el,
                "page type": ptype,
                "objects": shown,
                "stage": "saved/%s" % el,
                **_dialogs_since(ctx, mark),
            }
        )
        _emit_stage(ctx, unit, proj, lib, "saved/%s" % el, [el], "CtDraw's open + save of %s" % el)
    ctx.fresh_builder("the load + save of the marker symbols, as a later session")
    calls.append({"fresh Graphics Builder": True, "library": "as after the saves"})
    _follow(ctx, unit, proj, lib, names, "fresh", calls)
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (proj, lib), "calls": calls},
        "The COM calls of the unit in order (the stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


def _run_array_token(ctx: Oracle, unit: Unit) -> None:
    """Q28 (d): an array text whose row 0 is a parameter left unbound.
    In fx_plain: DrawText of '%Array0%', an array display value with the rows '%Array0%',
    '%Array1%', '%Array2%', saved; a placement binding Array1 only in a fresh Graphics
    Builder; the page's load + save there and in another fresh one."""
    lp, glib, gel = STATE_GENIE
    lib = glib + ".ctm"
    page = unit.data["page"]
    calls: list = []
    ctx.open_element(lp, lib, gel)
    text, x, y = ARRAY_TEXT
    before = _objects(ctx)
    ctx.com.DrawText(text, x, y)
    ctx.wait(0.5)
    an = _drawn(ctx, before, "Text", "DrawText(%r)" % text)
    _select_an(ctx, an)
    ctx.com.PropertiesDisplayValuePut(2, ARRAY_EXPR, "", "", "", "")
    for i, row in enumerate(ARRAY_ROWS):
        ctx.com.PropertiesDisplayValueTextPut(i, row)
    ctx.wait(0.5)
    got: dict = {}
    try:
        v = ctx.com.PropertiesDisplayValueGet()
        got["PropertiesDisplayValueGet"] = list(v) if isinstance(v, (tuple, list)) else v
    except Exception as e:  # noqa: BLE001
        got["PropertiesDisplayValueGet"] = "error: %s" % (e,)
    ctx.save()
    ctx.com.PageClose()
    calls.append(
        {
            "open": gel,
            "DrawText": [text, x, y],
            "AN": an,
            "PropertiesDisplayValuePut": [2, ARRAY_EXPR, "", "", "", ""],
            "PropertiesDisplayValueTextPut": list(ARRAY_ROWS),
            "getters": got,
            "stage": "genie",
        }
    )
    _emit_stage(ctx, unit, lp, lib, "genie", [gel], "the array text drawn in %s and %s saved" % (gel, gel))
    ctx.fresh_builder("a placement of %s, as a later session" % gel)
    calls.append({"fresh Graphics Builder": True, "library": "as after genie"})
    placed = _place_on_blank(ctx, unit, page, ARRAY_PLACE, "placed", calls)
    _load_save_page(
        ctx, unit, "FxMain", page, "reopened", calls, "its load + save in the Graphics Builder that placed it"
    )
    _put_file7(ctx, "FxMain", page + ".ctg", placed)
    ctx.fresh_builder("the page's load + save, as a later session")
    calls.append({"fresh Graphics Builder": True, "page": "as placed"})
    _load_save_page(ctx, unit, "FxMain", page, "fresh", calls, "its load + save in a fresh Graphics Builder")
    ctx.emit_json(
        unit,
        "actions.json",
        {"library": "%s/%s" % (lp, lib), "calls": calls},
        "The COM calls of the unit in order, with what CtDraw's getters said (the "
        "stages name their folders).",
        unit.sources[0],
    )
    ctx.collect(unit, skip={"%s/%s" % (lp, lib)} | _page_skip("FxMain", page))


def _bag_with(bag, changes: dict):
    """A copy of ``bag`` with ``changes`` (key -> value, None removes the key; an empty value
    is kept, as a stored '' is)."""
    out = bag.copy()
    for key, val in changes.items():
        k = next((i for i, (a, _b) in enumerate(out.pairs) if a.lower() == key.lower()), None)
        if val is None:
            if k is not None:
                del out.pairs[k]
        elif k is not None:
            out.pairs[k] = (out.pairs[k][0], val)
        else:
            out.pairs.append((key, val))
    return out


def _legacy_page(data: bytes) -> Tuple[bytes, List[dict]]:
    """A blank page with LEGACY_NUMBERS and LEGACY_STRINGS: copies of the Number and the String
    of ``data`` (LEGACY_FROM) with their value changes, named FxNum.. / FxStr.. (their
    __AN_NAME__), handles 0 .. in order, in two columns; the stored extent the copy's or
    LEGACY_ODD."""
    from ctgedit.format import genie_place as GP, legacy, template_place as TP, writer
    from ctgedit.format.page import parse_ctg_bytes

    src = parse_ctg_bytes(data).page.objects
    bases = {tc: [po for po in src if po.type_code == tc] for tc in (0x1004, 0x1005)}
    if any(len(v) != 1 for v in bases.values()):
        raise UnitFailed(
            "%s holds %s Numbers and Strings, not one each"
            % (LEGACY_FROM, {hex(k): len(v) for k, v in bases.items()})
        )
    objs, made = [], []
    h = 0
    for col, (stem, tc, variants) in enumerate(
        (("FxNum", 0x1004, LEGACY_NUMBERS), ("FxStr", 0x1005, LEGACY_STRINGS))
    ):
        for k, (changes, odd) in enumerate(variants):
            name = "%s%02d" % (stem, k)
            r = GP.decode(bases[tc][0])
            vals = _bag_with(r.entries[0][1], dict(changes, __AN_NAME__=name))
            r.entries = [[r.entries[0][0], vals, r.entries[0][2]]] + list(r.entries[1:])
            if r.name:
                r.name = name
            r.cf = 0  # (FxLegacy2's String holds a child: the copies are leaves)
            r.handle = h
            h += 1
            po = GP.to_po(GP.encode(r))
            legacy.place_point(po, 20 + 520 * col, 40 + 50 * k)
            if odd:
                legacy.set_point_extent(po, LEGACY_ODD)
            g = GP.decode(po)
            objs.append(po)
            made.append(
                {
                    "name": name,
                    "type_code": tc,
                    "changes": dict(changes),
                    "odd extent": odd,
                    "anchor": list(g.anchor),
                    "rect2": list(g.rect2),
                    "values": [list(p) for p in g.entries[0][1].pairs],
                }
            )
    from ctgedit.format import integrity

    pc = parse_ctg_bytes(TP.new_page_bytes(1024, 768, background=(0xD8D8D8, 0xD8D8D8)))
    pc.page.objects = objs
    out = writer.serialize_ctg(pc.container, pc.page, pc.attributes)
    bad = integrity.problems(parse_ctg_bytes(out), len(out))
    if bad:
        raise UnitFailed("the legacy page the tool made: %s" % "; ".join(bad))
    return out, made


def _run_legacy_measure(ctx: Oracle, unit: Unit) -> None:
    """Q28 (e): the extents CtDraw derives for legacy Numbers (0x1004)
    and Strings (0x1005). A page the tool makes of FxLegacy2's two with fonts, expressions,
    texts and stored extents changed; its load + save in a fresh Graphics Builder, and
    again."""
    page = unit.data["page"]
    src = _read(os.path.join(FIXTURES, "User", "FxMain", LEGACY_FROM + ".ctg"))
    if src is None:
        raise UnitFailed("the fixture has no FxMain\\%s.ctg" % LEGACY_FROM)
    data, made = _legacy_page(src)
    _put_file7(ctx, "FxMain", page + ".ctg", data)
    ctx.emit(
        unit,
        "input/page.ctg",
        data,
        "The page the tool made: %d legacy records from %s's Number and String." % (len(made), LEGACY_FROM),
        unit.sources[0],
    )
    calls: list = [{"made": made, "stage": "input"}]
    mark = _dialog_mark(ctx)
    _load_save_page(ctx, unit, "FxMain", page, "loaded", calls, "its load + save in a fresh Graphics Builder")
    calls[-1].update(_dialogs_since(ctx, mark))
    ctx.fresh_builder("the page's second load + save, as a later session")
    calls.append({"fresh Graphics Builder": True, "page": "as after loaded"})
    _load_save_page(
        ctx, unit, "FxMain", page, "again", calls, "its second load + save, in a fresh Graphics Builder"
    )
    ctx.emit_json(
        unit,
        "actions.json",
        {"page": "FxMain/%s" % page, "dpi": _screen_dpi(), "calls": calls},
        "The records the tool made, the COM calls of the unit in order (the stages "
        "name their folders) and the screen's DPI.",
        unit.sources[0],
    )
    ctx.collect(unit, skip=_page_skip("FxMain", page))


def _shipped_copy(ctx: Oracle, project: str, name: str, into: str, calls: list) -> Tuple[str, Optional[str]]:
    """The install's ``project``\\``name`` copied into the capture's ``into`` (refused when
    ``into`` has a file of that name): (the install's path, its sha1)."""
    path = _find_file(os.path.join(ctx.inst.user, project), name)
    if path is None:
        raise UnitFailed("the install's %s has no %s" % (project, name))
    if _find_file(os.path.join(ctx.inst.user, into), name) is not None:
        raise UnitFailed("%s already has a %s" % (into, name))
    sha = file_sha1(path)
    shutil.copyfile(path, os.path.join(ctx.inst.user, into, os.path.basename(path)))
    calls.append(
        {
            "copied": "%s/%s -> %s/%s" % (project, os.path.basename(path), into, os.path.basename(path)),
            "sha1": sha,
        }
    )
    return path, sha


def _shipped_unchanged(ctx: Oracle, real: Dict[str, Tuple[str, Optional[str]]]) -> List[str]:
    changed = []
    for name, (p, sha) in real.items():
        if file_sha1(p) != sha:
            changed.append(name)
            ctx.log(
                "The install's %s changed during the unit: it is NOT put back by the tool (check it)." % p
            )
    return changed


def _run_shipped_values(ctx: Oracle, unit: Unit) -> None:
    """Shipped elements whose own value bag differs from their templates."""
    if _inside(ctx.out_root, ORACLE):
        raise UnitFailed(
            "this unit captures AVEVA content: run it with --out outside tests\\fixtures\\oracle"
        )
    proj = unit.data["project"]
    calls: list = []
    real: Dict[str, Tuple[str, Optional[str]]] = {}
    files: Dict[str, str] = {}
    changed: List[str] = []
    try:
        for p, f in [e[:2] for e in SHIPPED7_ELEMENTS] + list(SHIPPED7_EXTRA):
            real["%s/%s" % (p, f)] = got = _shipped_copy(ctx, p, f, proj, calls)
            files[f] = os.path.basename(got[0])
        for stage in ("saved", "again"):
            if stage == "again":
                ctx.fresh_builder("the elements' second load + save, as a later session")
                calls.append({"fresh Graphics Builder": True, "library": "as after saved"})
            for _p, f, el in SHIPPED7_ELEMENTS:
                if stage == "saved":
                    _emit_stage(
                        ctx,
                        unit,
                        proj,
                        files[f],
                        "source/%s" % el,
                        [el],
                        "the copy from the install",
                        library=False,
                    )
                mark = _dialog_mark(ctx)
                ptype = ctx.open_element(proj, files[f], el)
                ctx.save()
                ctx.com.PageClose()
                calls.append(
                    {
                        "open + save": el,
                        "library": files[f],
                        "page type": ptype,
                        "stage": "%s/%s" % (stage, el),
                        **_dialogs_since(ctx, mark),
                    }
                )
                _emit_stage(
                    ctx,
                    unit,
                    proj,
                    files[f],
                    "%s/%s" % (stage, el),
                    [el],
                    "CtDraw's open + save of %s (%s)" % (el, stage),
                    library=False,
                )
    finally:
        changed = _shipped_unchanged(ctx, real)
        calls.append({"install files unchanged": not changed})
        ctx.emit_json(
            unit,
            "actions.json",
            {"project": proj, "calls": calls},
            "The COM calls of the unit in order (the stages name their folders).",
        )
    if changed:
        raise UnitFailed("the install's %s changed during the unit" % ", ".join(changed))
    ctx.collect(unit, skip={"%s/%s" % (proj, b) for b in files.values()})


def _run_shipped_about(ctx: Oracle, unit: Unit) -> None:
    """Q28 (c) on shipped content: Example !About copied into FxMain
    (the install's file checked unchanged after): CtDraw's load + save at this session's DPI,
    and again in a fresh Graphics Builder. Its output holds AVEVA content: it refuses the
    committed oracle folder."""
    if _inside(ctx.out_root, ORACLE):
        raise UnitFailed(
            "this unit captures AVEVA content: run it with --out outside tests\\fixtures\\oracle"
        )
    project, page = ABOUT_PAGE
    calls: list = [{"dpi": _screen_dpi()}]
    real: Dict[str, Tuple[str, Optional[str]]] = {}
    changed: List[str] = []
    try:
        real["%s/%s.ctg" % ABOUT_PAGE] = _shipped_copy(ctx, project, page + ".ctg", "FxMain", calls)
        ctx.emit(
            unit,
            "source/page.ctg",
            ctx.read("FxMain", page + ".ctg") or b"",
            "%s.ctg as the install holds it." % page,
        )
        _load_save_page(ctx, unit, "FxMain", page, "saved", calls, "its load + save")
        ctx.fresh_builder("the page's second load + save, as a later session")
        calls.append({"fresh Graphics Builder": True, "page": "as after saved"})
        _load_save_page(
            ctx, unit, "FxMain", page, "again", calls, "its second load + save, in a fresh Graphics Builder"
        )
    finally:
        changed = _shipped_unchanged(ctx, real)
        calls.append({"install files unchanged": not changed})
        ctx.emit_json(
            unit,
            "actions.json",
            {"page": "FxMain/%s" % page, "calls": calls},
            "The COM calls of the unit in order (the stages name their folders) and the screen's DPI.",
        )
    if changed:
        raise UnitFailed("the install's %s changed during the unit" % ", ".join(changed))
    ctx.collect(unit, skip=_page_skip("FxMain", page))


def _try_call(ctx: Oracle, rec: dict, key: str, fn, *args) -> bool:
    """``fn(*args)``, its result or error in ``rec[key]`` and the seconds it took; whether it
    returned (the builder may end behind it: the watchdog)."""
    t0 = time.time()
    try:
        r = fn(*args)
        rec[key] = "returned %r" % (r,)
        ok = True
    except Exception as e:  # noqa: BLE001
        rec[key] = "error: %s" % (e,)
        ok = False
    rec[key + " seconds"] = round(time.time() - t0, 1)
    rec[key + ": builder ended"] = ctx.builder_gone()
    return ok


def _run_self_template(ctx: Oracle, unit: Unit) -> None:
    """A template saved over itself."""
    from ctgedit.format.element import LibraryFile

    proj, style = OUTER_PROJECT, OUTER_STYLE
    lib = style + ".ctt"
    base, res, title = OUTER_BASE
    inner = _template_name(base, "XGA", title)
    source = ctx.read(proj, lib)
    calls: list = []

    def save_over(frm: str, stage: str) -> bool:
        before = ctx.read(proj, lib)
        rec: dict = {"PageNewTemplate": [proj, style, frm, res, title, True], "stage": stage}
        ctx.project(proj)
        ctx.used = True
        with contextlib.suppress(Exception):
            ctx.com.PageClose()
        mark = _dialog_mark(ctx)
        if _try_call(ctx, rec, "new", ctx.com.PageNewTemplate, proj, style, frm, res, title, True):
            ctx.wait(1.0)
            rec["PageName"] = ctx.page_name()
            tries = []
            for name in SELF_NAMES:
                t: dict = {"PageSaveAsEx": [proj, style, name]}
                with ctx.answer("Yes"):
                    _try_call(ctx, t, "save", ctx.com.PageSaveAsEx, proj, style, name)
                ctx.wait(1.0)
                t["library changed"] = ctx.read(proj, lib) != before
                tries.append(t)
                if t["library changed"] or ctx.builder_gone():
                    break
            rec["tries"] = tries
            if not ctx.builder_gone():
                rec["PageName after"] = ctx.page_name()
                with contextlib.suppress(Exception):
                    ctx.com.PageClose()
        rec.update(_dialogs_since(ctx, mark))
        data = ctx.read(proj, lib)
        rec["library changed"] = data != before
        if data is not None and data != before:
            lf = LibraryFile(data)
            names = lf.element_names()
            rec["elements"] = names
            slot = lf.find(inner)
            block = lf.block_data(slot).lower() if slot is not None else b""
            rec["%s names itself" % inner] = ("%s.%s.<ctt" % (style, inner)).lower().encode("cp1252") in block
            _emit_stage(
                ctx,
                unit,
                proj,
                lib,
                stage,
                [
                    n
                    for n in names
                    if n == inner or n.lower().startswith(base.lower()) or n.lower().startswith(OUTER_NAME)
                ],
                "PageNewTemplate from %s, linked, and PageSaveAsEx under %s's name" % (frm, inner),
            )
        calls.append(rec)
        return rec["library changed"]

    def follow_up(stage: str) -> None:
        """The load + save of the template that may hold itself, and a New Page from it, in a
        fresh Graphics Builder (each call watched)."""
        ctx.fresh_builder("the load + save of %s after %s" % (inner, stage), force=ctx.builder_gone())
        rec: dict = {"fresh Graphics Builder": True, "stage": "%s/reopened" % stage}
        mark = _dialog_mark(ctx)
        before = ctx.read(proj, lib)
        try:
            rec["page type"] = ctx.open_element(proj, lib, inner)
            rec["open"] = "returned"
        except Exception as e:  # noqa: BLE001
            rec["open"] = "error: %s" % (e,)
        rec["open: builder ended"] = ctx.builder_gone()
        if rec["open"] == "returned" and not ctx.builder_gone():
            with contextlib.suppress(Exception):
                rec["objects"] = _objects(ctx)
            _try_call(ctx, rec, "save", ctx.save)
            with contextlib.suppress(Exception):
                ctx.com.PageClose()
        rec.update(_dialogs_since(ctx, mark))
        data = ctx.read(proj, lib)
        rec["library changed"] = data != before
        if data is not None and data != before:
            _emit_stage(
                ctx,
                unit,
                proj,
                lib,
                "%s/reopened" % stage,
                [inner],
                "CtDraw's open + save of %s in a fresh Graphics Builder" % inner,
                library=False,
            )
        calls.append(rec)
        if ctx.builder_gone():
            return
        mark = _dialog_mark(ctx)
        page = "FxOracleSelf%s" % ("Outer" if "outer" in stage else "")
        with ctx.ok_dialogs() as answered:
            try:
                _new_page_from(ctx, unit, style, base, res, title, page, "%s/new_page" % stage, calls)
            except Exception as e:  # noqa: BLE001
                calls.append({"New Page": "error: %s" % (e,), "stage": "%s/new_page" % stage})
        calls.append(
            {
                "dialogs answered OK": answered,
                "builder ended": ctx.builder_gone(),
                "stage": "%s/new_page" % stage,
                **_dialogs_since(ctx, mark),
            }
        )

    try:
        if save_over(base, "self"):
            follow_up("self")
        _put_file7(ctx, proj, lib, source)
        ctx.fresh_builder(
            "fxouter made and saved over %s, from the library as it was" % inner, force=ctx.builder_gone()
        )
        calls.append({"fresh Graphics Builder": True, "library": "as it was"})
        made = _make_outer(ctx, unit, calls)
        if save_over(made.split("@")[0], "outer_self"):
            follow_up("outer_self")
    finally:
        ctx.emit_json(
            unit,
            "actions.json",
            {"library": "%s/%s" % (proj, lib), "calls": calls},
            "The COM calls of the unit in order, how long each took and what CtDraw "
            "answered (the stages name their folders).",
            unit.sources[0],
        )
    ctx.collect(
        unit, skip={"%s/%s" % (proj, lib)} | _page_skip("FxMain", "FxOracleSelf", "FxOracleSelfOuter")
    )


def _run_self_placeholder(ctx: Oracle, unit: Unit) -> None:
    """A genie holding a placeholder of itself."""
    from ctgedit.format.element import LibraryFile, element_content, open_element

    proj, lib = unit.data["project"], unit.data["library"]
    src_lib, src_el = SELF_HOLDER_FROM
    held = _oracle_file(*SELF_HOLDER_CAPTURE)
    hlf = LibraryFile(held)
    if b"_library" not in hlf.block_data(hlf.find(src_el)).lower():
        raise UnitFailed("%s's %s holds no placeholder" % ("/".join(SELF_HOLDER_CAPTURE), src_el))
    content = element_content(open_element(src_lib, src_el, library=hlf))
    source = ctx.read(proj, lib)
    calls: list = []
    try:
        for k, name in enumerate(SELF_HOLDER_AS):
            stage = "%s_holds" % name
            if k:
                # (forced: a builder whose open failed may still hold the library open)
                ctx.fresh_builder("the open + save of %s holding the placeholder" % name, force=True)
            lf = LibraryFile(source)
            lf.write_element(name, content)
            made = lf.to_bytes()
            _put_file7(ctx, proj, lib, made)
            _emit_stage(
                ctx,
                unit,
                proj,
                lib,
                "%s/input" % stage,
                [name],
                "%s with %s's content "
                "(%s) written in as %s by the tool" % (lib, src_el, "/".join(SELF_HOLDER_CAPTURE), name),
            )
            rec: dict = {
                "element": name,
                "copied from": "%s %s" % ("/".join(SELF_HOLDER_CAPTURE), src_el),
                "stage": stage,
            }
            mark = _dialog_mark(ctx)
            t0 = time.time()
            # (the genie page type: CtDraw's own answer kept, no other type tried after a
            # failure)
            stem = os.path.splitext(lib)[0]
            ctx.project(proj)
            ctx.used = True
            ctx.opened.add((stem.lower(), name.lower()))
            with contextlib.suppress(Exception):
                ctx.com.PageClose()
            rec["PageOpenEx"] = [proj, stem, name, GENIE_PAGE_TYPE]
            try:
                ctx.com.PageOpenEx(proj, stem, name, GENIE_PAGE_TYPE)
                ctx.wait(1.0)
                got = ctx.page_name()
                rec["open"] = "returned" if name.lower() in got.lower() else "returned, PageName %r" % got
            except Exception as e:  # noqa: BLE001
                rec["open"] = "error: %s" % (e,)
            rec["open seconds"] = round(time.time() - t0, 1)
            rec["open: builder ended"] = ctx.builder_gone()
            if not ctx.builder_gone():
                try:
                    rec["PageName after the open"] = str(ctx.com.PageName())
                except Exception as e:  # noqa: BLE001
                    rec["PageName after the open"] = "error: %s" % (e,)
            if rec["open"] == "returned" and not ctx.builder_gone():
                with contextlib.suppress(Exception):
                    rec["objects"] = _objects(ctx)
                _try_call(ctx, rec, "save", ctx.save)
                with contextlib.suppress(Exception):
                    ctx.com.PageClose()
            rec.update(_dialogs_since(ctx, mark))
            data = ctx.read(proj, lib)
            rec["library changed"] = data != made
            if data is not None and data != made:
                _emit_stage(
                    ctx,
                    unit,
                    proj,
                    lib,
                    "%s/saved" % stage,
                    [name],
                    "CtDraw's open + save of %s holding the placeholder" % name,
                )
            calls.append(rec)
    finally:
        ctx.emit_json(
            unit,
            "actions.json",
            {"library": "%s/%s" % (proj, lib), "calls": calls},
            "The COM calls of the unit in order, how long each took and what CtDraw "
            "answered (the stages name their folders).",
            unit.sources[0],
        )
    ctx.collect(unit, skip={"%s/%s" % (proj, lib)})


# ============================================================================ the command
def cmd_run(args) -> int:
    from ctgedit.infra import install

    # the whole run (Studio's start, the compiler) looks at the install the change goes into
    if args.install_root:
        os.environ[install.ENV] = os.path.abspath(args.install_root)
    else:
        os.environ.pop(install.ENV, None)
    if os.environ.get("CTG_NO_STUDIO") == "1" and not args.dry_run and not args.install_root:
        raise Refused("CTG_NO_STUDIO=1 is set: the capture needs Plant SCADA Studio.")
    inst = locate(args.install_root)
    run_dir = os.path.join(args.backup_root or default_backup_root(), _stamp())
    trial = bool(args.install_root)
    # a trial on a fake install runs no Citect program, so none of them counts there
    change = InstallChange(inst, run_dir, ini_edits=not args.no_ini_edits, processes=dict if trial else None)
    units = select_units(args.steps)
    if args.dry_run:
        _say("DRY RUN: nothing is changed.")
        for line in change.plan():
            _say("  " + line)
        _say("Units (%d), written into %s:" % (len(units), os.path.abspath(args.out)))
        for u in units:
            _say("  %s: %s" % (u.key, u.what))
        _say(
            "Each step starts in a fresh Graphics Builder (a restart ends %s), and so does a "
            "unit or an element that names an element the builder has opened."
            % ("CtDraw32" if args.restart == "ctdraw" else "Studio and its CtDraw32")
        )
        if trial:
            _say("A trial on a fake install: the COM capture is skipped.")
        _say("Then the restore and its checks.")
        return 0
    change.preflight()
    os.makedirs(run_dir, exist_ok=True)
    prep_dir = os.path.join(run_dir, "prepared")
    prepared = {} if trial else run_prepare(prep_dir)
    stop = StopFlag()
    old = signal.signal(signal.SIGINT, stop.handler)
    oracle = None
    ok = False
    t0 = time.time()
    try:
        change.apply()
        if trial:
            _say(
                "A trial on a fake install: the COM capture is skipped (it would start the "
                "registered CtDraw and Studio, which work on the real install's citect.ini)."
            )
        else:
            oracle = Oracle(
                change,
                args.out,
                prepared,
                prep_dir,
                shots=args.shots,
                stop=stop,
                raw_tables=args.raw_tables,
                com_deadline=args.com_deadline,
                restart=args.restart,
            )
            oracle.take_baseline()
            oracle.start()
            oracle.run_units(units)
            oracle.write_manifest()
    except KeyboardInterrupt:
        _say("Stopped.")
    except Refused:
        raise
    except Exception:  # noqa: BLE001
        _say("The capture failed:\n" + traceback.format_exc())
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if oracle is not None:
                oracle.close()
            if change.state:
                if not trial:
                    try:
                        stop_studio(change.log)
                    except Exception as e:  # noqa: BLE001 (the restore checks again)
                        change.log("Stopping Studio: %s" % e)
                try:
                    change.note_seen()
                    ok = change.restore()
                except BaseException as e:
                    _still_changed(run_dir, e)
                    raise
        finally:
            signal.signal(signal.SIGINT, old)
    if oracle is not None:
        got = sum(1 for st in oracle.ran.values() if st == "captured")
        _say(
            "%d of %d units captured in %.0f s; %d failed%s. The Graphics Builder was "
            "restarted %d times."
            % (
                got,
                len(units),
                time.time() - t0,
                len(oracle.failures),
                (": " + ", ".join(oracle.failures)) if oracle.failures else "",
                oracle.restarts,
            )
        )
        _say("The COM log: %s" % os.path.join(run_dir, "com.log"))
    return 0 if ok and (oracle is None or not oracle.failures) else 1


def _still_changed(run_dir: str, e: BaseException) -> None:
    """Say that a restore did not finish, why, and the command that finishes it."""
    _say(
        "THE INSTALL IS STILL CHANGED (the restore stopped: %s: %s). The Fx projects may "
        "still be registered. Run: python tools/live/fixture_oracle.py restore --backup "
        '"%s" --stop-studio' % (type(e).__name__, e, run_dir)
    )


def cmd_restore(args) -> int:
    if args.backup:
        runs = [args.backup]
    else:
        runs = open_runs(args.backup_root or default_backup_root())
        if not runs:
            _say("No run waits for a restore.")
            return 0
    ok = True
    for run in runs:
        _say("Restoring the run in %s" % run)
        try:
            ok = change_from_state(run).restore(stop=args.stop_studio, force=args.force) and ok
        except BaseException as e:
            _still_changed(run, e)
            raise
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The one-off CtDraw capture of the fixtures (docs/TESTING.md).")
    ap.add_argument("--list", action="store_true", help="the steps and the tests they feed")
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="change the install, capture, restore")
    run.add_argument("--steps", help="comma separated step or step:unit patterns")
    run.add_argument("--dry-run", action="store_true", help="print every change and unit")
    run.add_argument("--out", default=ORACLE, help="the output folder (tests/fixtures/oracle)")
    run.add_argument("--shots", choices=("listed", "all", "none"), default="listed")
    run.add_argument("--install-root", help="a fake install folder instead of the real one")
    run.add_argument("--backup-root", help="where the run folders go (the data folder)")
    run.add_argument(
        "--raw-tables", action="store_true", help="also keep the bytes of each changed table and index"
    )
    run.add_argument("--no-ini-edits", action="store_true", help="leave citect.ini as it is during the run")
    run.add_argument(
        "--com-deadline",
        type=float,
        default=600.0,
        help="seconds one COM call may take before CtDraw is ended (0: never)",
    )
    run.add_argument(
        "--restart",
        choices=("studio", "ctdraw"),
        default="studio",
        help="what a fresh Graphics Builder ends first: Studio and its CtDraw32 (default) or CtDraw32 alone",
    )
    rs = sub.add_parser("restore", help="put the install back after a crash")
    rs.add_argument("--backup", help="the run folder (default: every run not restored)")
    rs.add_argument("--backup-root", help="where the run folders are (the data folder)")
    rs.add_argument(
        "--stop-studio", action="store_true", help="close Plant SCADA Studio and the Graphics Builder first"
    )
    rs.add_argument(
        "--force", action="store_true", help="put the backups back even over files changed after the run"
    )
    pr = sub.add_parser("prepare", help=argparse.SUPPRESS)
    pr.add_argument("work")
    args = ap.parse_args(argv)
    try:
        if args.list:
            list_steps()
            return 0
        if args.cmd == "run":
            return cmd_run(args)
        if args.cmd == "restore":
            return cmd_restore(args)
        if args.cmd == "prepare":
            prepare(args.work)
            return 0
    except Refused as e:
        _say("Refused: %s" % e)
        return 2
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
