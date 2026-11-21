"""Where the Plant SCADA install is. All install lookups go through here."""

from __future__ import annotations

import glob
import os
import re
from typing import List, Optional

ENV = "CTG_SCADA_ROOT"
#: the value of CTG_SCADA_ROOT that means 'no install'
NONE = "none"

#: the real installs' citect.ini files (the fall back when the registry names none)
_INI_GLOB = r"C:\ProgramData\AVEVA Plant SCADA*\Config\citect.ini"
#: the real installs' Bin folders, by product family (the first family with a hit wins
#: for :func:`bin_dir`)
_BIN_GLOBS = (
    r"C:\Program Files (x86)\AVEVA Plant SCADA*\Bin",
    r"C:\Program Files (x86)\AVEVA\Plant SCADA*\Bin",
    r"C:\Program Files (x86)\Schneider Electric\Citect SCADA*\Bin",
)
#: the folders the Tools menu also looks in for a program (the 64-bit install)
_TOOL_DIRS = (r"C:\Program Files\AVEVA Plant SCADA\Bin",)
#: the real install's help pages (its HTML topics)
_DOCS = r"C:\ProgramData\AVEVA\Product Documentation\Plant SCADA\content\en"
#: the registry keys whose sub keys carry DefaultINIPath
_REG_KEYS = (
    r"SOFTWARE\WOW6432Node\Citect\SCADA Installs",
    r"SOFTWARE\Citect\SCADA Installs",
    r"SOFTWARE\WOW6432Node\AVEVA\Plant SCADA Installs",
    r"SOFTWARE\AVEVA\Plant SCADA Installs",
)

_USER_RE = re.compile(r"^\s*USER\s*=\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def mode() -> str:
    """'real' (CTG_SCADA_ROOT unset), 'fake' (a folder) or 'none'."""
    v = (os.environ.get(ENV) or "").strip()
    if not v:
        return "real"
    return "none" if v.lower() == NONE else "fake"


def is_overridden() -> bool:
    """Is CTG_SCADA_ROOT set (a fake install or none)?"""
    return mode() != "real"


def _fake_root() -> str:
    return os.path.abspath(os.environ[ENV].strip())


def _registry_ini() -> Optional[str]:
    """The real active install's citect.ini from the registry (``DefaultINIPath`` under
    ``HKLM\\SOFTWARE\\[WOW6432Node\\]Citect\\SCADA Installs\\<ver>``), or None."""
    try:
        import winreg
    except ImportError:
        return None
    for base in _REG_KEYS:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except OSError:
            continue
        try:
            n = winreg.QueryInfoKey(key)[0]
            for i in range(n):
                ver = winreg.EnumKey(key, i)
                try:
                    sub = winreg.OpenKey(key, ver)
                    path, _t = winreg.QueryValueEx(sub, "DefaultINIPath")
                    winreg.CloseKey(sub)
                    if path and os.path.isfile(path):
                        return path
                except OSError:
                    continue
        finally:
            winreg.CloseKey(key)
    return None


def registry_ini() -> Optional[str]:
    """The citect.ini the install itself names as active: the registry's in the real mode,
    ``<root>\\Config\\citect.ini`` of a fake install, None with no install."""
    m = mode()
    if m == "none":
        return None
    if m == "fake":
        p = os.path.join(_fake_root(), "Config", "citect.ini")
        return p if os.path.isfile(p) else None
    return _registry_ini()


def candidate_inis() -> List[str]:
    """Every citect.ini that may be the active one when the registry names none (the real
    installs' Config folders; the fake install's own; none)."""
    m = mode()
    if m == "none":
        return []
    if m == "fake":
        p = registry_ini()
        return [p] if p else []
    return glob.glob(_INI_GLOB)


def ini_user(ini_path: str) -> Optional[str]:
    """The ``[CtEdit] USER=`` folder of one citect.ini, or None."""
    from ..format.textcodec import CODEC

    try:
        with open(ini_path, encoding=CODEC, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    # only the [CtEdit] section, so a USER= elsewhere cannot match
    low = text.lower()
    start = low.find("[ctedit]")
    if start < 0:
        return None
    end = low.find("\n[", start + 1)
    section = text[start : end if end > 0 else len(text)]
    m = _USER_RE.search(section)
    if not m:
        return None
    path = m.group(1).strip().strip('"')
    return path or None


def user_dir() -> Optional[str]:
    """The active install's User folder, or None."""
    m = mode()
    if m == "none":
        return None
    reg = registry_ini()
    if reg:
        user = ini_user(reg)
        if user and os.path.isdir(user):
            return user
    if m == "fake":
        user = os.path.join(_fake_root(), "User")
        return user if os.path.isdir(user) else None
    found = []
    for ini in candidate_inis():
        user = ini_user(ini)
        if user and os.path.isdir(user):
            try:
                mtime = os.path.getmtime(ini)
            except OSError:
                mtime = 0
            found.append((mtime, user))
    if not found:
        return None
    found.sort(reverse=True)  # the newest config wins
    return found[0][1]


def active_ini() -> Optional[str]:
    """The active install's citect.ini (the one :func:`registry_ini` names, else the Config
    folder next to the User folder), or None."""
    ini = registry_ini()
    if not ini and mode() == "real":
        user = user_dir()
        ini = os.path.join(os.path.dirname(user), "Config", "citect.ini") if user else None
    return ini if ini and os.path.isfile(ini) else None


def root() -> Optional[str]:
    """The install's data folder (the one holding Config and User), or None."""
    m = mode()
    if m == "none":
        return None
    if m == "fake":
        return _fake_root()
    ini = active_ini()
    if ini:
        return os.path.dirname(os.path.dirname(ini))
    user = user_dir()
    return os.path.dirname(user) if user else None


def bin_dirs() -> List[str]:
    """The Plant SCADA / Citect Bin folders, oldest first by name (CTG_SCADA_BIN: that one
    only; a fake install: its Bin folder when it has one)."""
    env = os.environ.get("CTG_SCADA_BIN")
    if env:
        return [env]
    m = mode()
    if m == "none":
        return []
    if m == "fake":
        b = os.path.join(_fake_root(), "Bin")
        return [b] if os.path.isdir(b) else []
    out: List[str] = []
    for pat in _BIN_GLOBS:
        out.extend(sorted(p for p in glob.glob(pat) if os.path.isdir(p)))
    return out


def bin_dir() -> Optional[str]:
    """THE Bin folder (the compiler's, the runtime's): CTG_SCADA_BIN, a fake install's, else
    the newest of the first product family installed; None when there is none."""
    env = os.environ.get("CTG_SCADA_BIN")
    if env:
        return env
    m = mode()
    if m != "real":
        dirs = bin_dirs()
        return dirs[-1] if dirs else None
    for pat in _BIN_GLOBS:
        hits = [p for p in glob.glob(pat) if os.path.isdir(p)]
        if hits:
            return sorted(hits)[-1]
    return None


def bin_dirs_newest_first() -> List[str]:
    """The Bin folders to search, best first: CTG_SCADA_BIN first when set; then, in the
    real mode, every install by product family (the family order of :func:`bin_dir`, so
    AVEVA Plant SCADA before Citect SCADA) and the newest first within each family. In
    the real mode CTG_SCADA_BIN does not hide the installs: they stay the fall back."""
    env = os.environ.get("CTG_SCADA_BIN")
    out: List[str] = [env] if env else []
    if mode() != "real":
        return out or list(reversed(bin_dirs()))
    for pat in _BIN_GLOBS:
        out += [p for p in sorted(glob.glob(pat), reverse=True) if os.path.isdir(p) and p not in out]
    return out


def tool_dirs() -> List[str]:
    """The folders a Tools menu program is looked for in (each one level down too): the Bin
    folders, best first (:func:`bin_dirs_newest_first`), and in the real mode the 64-bit
    install's."""
    out = bin_dirs_newest_first()
    if mode() == "real":
        out += [d for d in _TOOL_DIRS if d not in out]
    return out


def docs_dir() -> Optional[str]:
    """The install's help pages folder (CTG_AVEVA_HELP wins; a fake install's Docs), or
    None with no install."""
    env = os.environ.get("CTG_AVEVA_HELP")
    if env:
        return env
    m = mode()
    if m == "none":
        return None
    if m == "fake":
        return os.path.join(_fake_root(), "Docs")
    return _DOCS


def zlib_dll() -> Optional[str]:
    """The install's classic zlib (``Bin\\Bin (x64)\\zlib.dll``: the deflate CtDraw's .ctF
    files are written with), best install first (:func:`bin_dirs_newest_first`), or None."""
    for b in bin_dirs_newest_first():
        p = os.path.join(b, "Bin (x64)", "zlib.dll")
        if os.path.isfile(p):
            return p
    return None


def func0_dbf() -> Optional[str]:
    """FUNC0.DBF of the Bin folders (best first: :func:`bin_dirs_newest_first`), or None."""
    for b in bin_dirs_newest_first():
        for name in ("FUNC0.DBF", "func0.dbf"):
            p = os.path.join(b, name)
            if os.path.isfile(p):
                return p
    return None
