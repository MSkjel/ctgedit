"""ActiveX controls: registry and type library info, and the controls a project uses."""

from __future__ import annotations

import functools
import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from ..infra.logsetup import note_exception as _note_exc

ACTIVEX_TC = 0x0D
from ..format.cfb import SIGNATURE as _CFB

#: ProcessAnalyst.ChartControl.1 (Citect Process Analyst Control)
PROCESS_ANALYST_CLSID = "{37D4F22A-7950-45DF-B1F6-DD78E503D7A9}"

# FUNCDESC / VARDESC flags (oaidl.h)
_F_RESTRICTED, _F_BINDABLE, _F_HIDDEN = 0x1, 0x4, 0x40
_V_READONLY, _V_BINDABLE, _V_HIDDEN, _V_RESTRICTED = 0x1, 0x4, 0x40, 0x80
_INVOKE_FUNC, _INVOKE_GET, _INVOKE_PUT, _INVOKE_PUTREF = 1, 2, 4, 8


@dataclass(frozen=True)
class ControlInfo:
    clsid: str
    name: str = ""
    progid: str = ""
    server: str = ""
    typelib: str = ""
    version: str = ""

    @property
    def label(self) -> str:
        return self.name or self.progid or self.clsid


@dataclass
class ControlMember:
    name: str
    vt: int = 0xFFFF  # normalised variant type
    readable: bool = False
    writable: bool = False
    bindable: bool = False
    doc: str = ""

    @property
    def status(self) -> str:
        """CtDraw's 'Property status'."""
        if self.readable and self.writable:
            return "Read/Write"
        return "Read-only" if self.readable else "Write-only"


@dataclass
class ControlMembers:
    properties: List[ControlMember] = field(default_factory=list)
    events: List[str] = field(default_factory=list)


def _reg(path: str, value: str = "") -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path, 0, winreg.KEY_READ | view) as k:
                v = winreg.QueryValueEx(k, value)[0]
                if isinstance(v, str):
                    return v
        except OSError:
            continue
    return None


def _machine_registry() -> bool:
    """Are the machine's control registrations read (the real install's mode)?"""
    from ..infra import install

    return install.mode() == "real"


def control_info(clsid: str) -> Optional[ControlInfo]:
    """The registered control ``clsid`` ('{...}'), None when it is not
    registered on this machine (or with a fake install or none)."""
    return _control_info(clsid) if _machine_registry() else None


def control_members(clsid: str) -> Optional[ControlMembers]:
    """The control's properties and events from its type library; None when
    it cannot be read (not registered, no type library, no pywin32; a fake
    install or none)."""
    return _control_members(clsid) if _machine_registry() else None


@functools.lru_cache(maxsize=256)
def _control_info(clsid: str) -> Optional[ControlInfo]:
    if not clsid:
        return None
    base = r"CLSID\%s" % clsid
    name = _reg(base)
    if name is None:
        return None
    return ControlInfo(
        clsid=clsid,
        name=name,
        progid=_reg(base + r"\ProgID") or "",
        server=_reg(base + r"\InprocServer32") or "",
        typelib=_reg(base + r"\TypeLib") or "",
        version=_reg(base + r"\Version") or "",
    )


def _normal_vt(ti, td) -> int:
    """The VARTYPE of a TYPEDESC, normalised: pointers as BYREF, arrays with the array
    flag, small and unsigned integers as long, aliases resolved."""
    if isinstance(td, tuple):
        vt, sub = td[0], td[1] if len(td) > 1 else None
    else:
        vt, sub = td, None
    if vt in (0, 1, 0x18):
        return 1
    if vt in (2, 3, 4, 5, 6, 7, 8, 9, 10, 0xB, 0xC, 0xD, 0x19):
        return vt
    if vt == 0xE:
        return 5
    if vt in (0x10, 0x11, 0x12, 0x13, 0x16, 0x17):
        return 3
    if vt == 0x1A and sub is not None:
        return _normal_vt(ti, sub) | 0x4000
    if vt == 0x1B and sub is not None:
        return _normal_vt(ti, sub) | 0x2000
    if vt == 0x1D:
        try:
            ref = ti.GetRefTypeInfo(sub)
            ta = ref.GetTypeAttr()
            kind = ta.typekind
            if kind == 0:  # TKIND_ENUM
                return 3
            if kind in (3, 4):  # TKIND_INTERFACE / DISPATCH
                return 9
            if kind == 6:  # TKIND_ALIAS
                return _normal_vt(ref, ta.tdescAlias)
        except Exception:  # noqa: BLE001 -- swallow-ok: a type the type info cannot follow: the default
            pass
        return 0x18
    return 0xFFFF


@functools.lru_cache(maxsize=64)
def _control_members(clsid: str) -> Optional[ControlMembers]:
    info = _control_info(clsid)
    if info is None:
        return None
    try:
        import pythoncom
        import pywintypes
    except ImportError:
        return None
    tl = None
    # InprocServer32 may be quoted or hold %SystemRoot% (REG_EXPAND_SZ)
    for src in (os.path.expandvars(info.server.strip().strip('"')),):
        if src and os.path.exists(src):
            try:
                tl = pythoncom.LoadTypeLib(src)
                break
            except Exception:  # noqa: BLE001 -- swallow-ok: try the next type library source
                tl = None
    if tl is None and info.typelib:
        try:
            major, _dot, minor = (info.version or "1.0").partition(".")
            tl = pythoncom.LoadRegTypeLib(pywintypes.IID(info.typelib), int(major or 1), int(minor or 0), 0)
        except Exception:  # noqa: BLE001 -- swallow-ok: no registered type library: no members
            return None
    if tl is None:
        return None
    try:
        co = tl.GetTypeInfoOfGuid(pywintypes.IID(clsid))
        ca = co.GetTypeAttr()
    except Exception:  # noqa: BLE001 -- swallow-ok: the type library lacks the class: no members
        return None
    out = ControlMembers()
    have: Dict[str, ControlMember] = {}
    for n in range(ca.cImplTypes):
        try:
            flags = co.GetImplTypeFlags(n)
            ref = co.GetRefTypeInfo(co.GetRefTypeOfImplType(n))
            ra = ref.GetTypeAttr()
        except Exception:  # noqa: BLE001 -- swallow-ok: an interface the type info cannot describe: skipped
            continue
        default, source = bool(flags & 1), bool(flags & 2)
        if not default:
            continue
        for f in range(ra.cFuncs):
            try:
                fd = ref.GetFuncDesc(f)
                name = ref.GetNames(fd.memid)[0]
            except Exception:  # noqa: BLE001 -- swallow-ok: a member the type info cannot describe: skipped
                continue
            if source:
                if (
                    fd.invkind == _INVOKE_FUNC
                    and not fd.wFuncFlags & _F_RESTRICTED
                    and name not in out.events
                ):
                    out.events.append(name)
                continue
            if fd.invkind not in (_INVOKE_GET, _INVOKE_PUT, _INVOKE_PUTREF) or fd.wFuncFlags & (
                _F_RESTRICTED | _F_HIDDEN
            ):
                continue
            m = have.get(name)
            if m is None:
                try:
                    doc = ref.GetDocumentation(fd.memid)[1] or ""
                except Exception:  # noqa: BLE001 -- swallow-ok: no doc string
                    doc = ""
                m = have[name] = ControlMember(name, doc=doc)
                out.properties.append(m)
            m.bindable = m.bindable or bool(fd.wFuncFlags & _F_BINDABLE)
            if fd.invkind == _INVOKE_GET:
                m.readable = True
                try:
                    m.vt = _normal_vt(ref, fd.rettype[0])
                except Exception:  # noqa: BLE001 -- swallow-ok: a type the type info cannot follow: the default
                    pass
            else:
                m.writable = True
                if m.vt == 0xFFFF and fd.args:
                    try:
                        m.vt = _normal_vt(ref, fd.args[-1][0])
                    except Exception:  # noqa: BLE001 -- swallow-ok: a type the type info cannot follow: the default
                        pass
        if source:
            continue
        for v in range(ra.cVars):
            try:
                vd = ref.GetVarDesc(v)
                name = ref.GetNames(vd.memid)[0]
            except Exception:  # noqa: BLE001 -- swallow-ok: a member the type info cannot describe: skipped
                continue
            if vd.wVarFlags & (_V_RESTRICTED | _V_HIDDEN) or name in have:
                continue
            m = have[name] = ControlMember(
                name,
                readable=True,
                writable=not vd.wVarFlags & _V_READONLY,
                bindable=bool(vd.wVarFlags & _V_BINDABLE),
            )
            try:
                m.vt = _normal_vt(ref, vd.elemdescVar[0])
            except Exception:  # noqa: BLE001 -- swallow-ok: a type the type info cannot follow: the default
                pass
            out.properties.append(m)
    return out


#: 'List Property Types': normalised VT -> compatible tag types
_COMPATIBLE = {
    0: "ALL TYPES",
    1: "ALL TYPES",
    0x18: "ALL TYPES",
    2: "INT, LONG, UINT, REAL, BCD, LONGBCD",
    3: "LONG, LONGBCD",
    10: "LONG, LONGBCD",
    0xC: "LONG, LONGBCD",
    0x13: "LONG, LONGBCD",
    4: "REAL",
    5: "REAL",
    6: "REAL",
    7: "REAL",
    8: "STRING",
    0xB: "BCD, BYTE, DIGITAL, INT, LONG, LONGBCD, REAL, UINT",
    0x11: "BYTE, INT, LONG, LONGBCD, UINT, REAL",
    0x12: "LONG, LONGBCD, REAL, UINT",
}


def compatible_types(vt: int) -> Optional[str]:
    """The Plant SCADA tag types a property of type ``vt`` can be associated
    with, None when there are none (the association fields are disabled)."""
    return _COMPATIBLE.get(vt)


def _tag_index(tag_type: str) -> int:
    t = (tag_type or "").strip().upper()
    return {
        "DIGITAL": 0,
        "INT": 1,
        "REAL": 2,
        "BCD": 3,
        "LONG": 4,
        "LONGBCD": 5,
        "UINT": 10,
        "STRING": 7,
    }.get(t, 8)


def tag_type_problem(member: ControlMember, tag: str, tag_type: str) -> Optional[str]:
    """CtDraw's Type Evaluation warning for associating ``member`` with a tag
    of ``tag_type`` (None = compatible or unknown)."""
    if not tag_type:
        return None
    vt, t = member.vt, _tag_index(tag_type)
    verdict = 0
    if vt in (0, 1, 0x18):
        verdict = 0
    elif vt == 2:
        verdict = 1 if t in (0, 8) else 0 if t in (1, 2, 3, 4, 5, 10) else 2
    elif vt in (3, 10, 0xC, 0x13):
        verdict = 1 if t in (0, 1, 2, 3, 8, 10) else 0 if t in (4, 5) else 2
    elif vt in (4, 5, 6, 7):
        verdict = 1 if t in (0, 1, 3, 4, 5, 8, 10) else 0 if t == 2 else 2
    elif vt == 8:
        verdict = 0 if t == 7 else 2
    elif vt == 0xB:
        verdict = 0 if t in (0, 1, 2, 3, 4, 5, 8, 10) else 2
    elif vt == 0x11:
        verdict = 1 if t == 0 else 0 if t in (1, 2, 3, 4, 5, 8, 10) else 2
    elif vt == 0x12:
        verdict = 1 if t in (0, 1, 3, 8) else 0 if t in (2, 4, 5, 10) else 2
    else:
        verdict = 3
    if verdict == 1:
        return "Associating the property '%s' with the smaller %s tag '%s' may lose some data." % (
            member.name,
            tag_type.upper(),
            tag,
        )
    if verdict == 2:
        return (
            "Associating the property '%s' with the %s tag '%s' (a different type) may "
            "make the Plant SCADA runtime unstable." % (member.name, tag_type.upper(), tag)
        )
    if verdict == 3:
        return (
            "The property '%s' has a type Plant SCADA does not support; associating it "
            "may make the runtime unstable." % member.name
        )
    return None


# -- controls already used by the project's pages ----------------------------------------
@dataclass(frozen=True)
class ControlDonor:
    clsid: str
    path: str  # the page holding a copy
    index: int  # the record index on that page
    name: str = ""  # its ACTIVEXEXT_*NAME / COMMENT, for the list
    size: Tuple[int, int] = (0, 0)

    @property
    def page(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]


_DONOR_CACHE: Dict[tuple, tuple] = {}


def _control_pages(folder: str) -> Optional[List[str]]:
    """The pages of ``folder`` that hold ActiveX objects, from the project's
    activex.DBF (the register's row per control: PAGE, AN, OBJ_NAME,
    OBJ_GUID) -- so only those pages are read; None without the table (then
    every page is scanned)."""
    from ..format.fileio import find_ci
    from .project import cached_rows

    table = find_ci(folder, "activex.dbf")
    if table is None:
        return None
    try:
        names = {f.lower(): f for f in os.listdir(folder) if f.lower().endswith(".ctg")}
    except OSError:
        return []
    out, seen = [], set()
    for row in cached_rows(table):
        page = (row.get("PAGE") or "").strip().lower()
        if page and page not in seen and page + ".ctg" in names:
            seen.add(page)
            out.append(os.path.join(folder, names[page + ".ctg"]))
    return sorted(out)


def project_controls(project_dir: str) -> Tuple[ControlDonor, ...]:
    """One top-level, free copy of every distinct ActiveX control on the
    pages of ``project_dir`` and its include chain (first found wins)."""
    from .project import chain_dirs

    try:
        dirs = chain_dirs(project_dir)
    except Exception:  # noqa: BLE001
        _note_exc("activex.project_controls", project=project_dir)
        dirs = [project_dir]
    files = []
    for d in dirs:
        pages = _control_pages(d)
        files.extend(pages if pages is not None else sorted(glob.glob(os.path.join(d, "*.ctg"))))
    sig = tuple((p, os.path.getmtime(p)) for p in files if os.path.exists(p))
    hit = _DONOR_CACHE.get(project_dir)
    if hit is not None and hit[0] == sig:
        return hit[1]
    from ..format import geometry as G
    from ..format.page import parse_ctg
    from .pgregister import ole_clsid

    seen, out = set(), []
    for path, _m in sig:
        try:
            with open(path, "rb") as fh:
                if _CFB not in fh.read():
                    continue
            pg = parse_ctg(path).page
        except Exception:  # noqa: BLE001
            _note_exc("activex.project_controls", page=path)
            continue
        if pg is None:
            continue
        from ..format.placement import forest_parents

        try:
            parents = forest_parents(pg.objects)
        except Exception:  # noqa: BLE001
            _note_exc("activex.project_controls", page=path)
            continue
        from ..format.page import geom_name_len

        for i, po in enumerate(pg.objects):
            if (
                po.type_code != ACTIVEX_TC
                or parents[i] is not None
                or not po.subtype_size & 0x2
                or po.subtype_size & 0x1
            ):
                continue
            o = geom_name_len(po.body_raw, 0)
            if not o or po.body_raw[o : o + 3] != b"\0\0\0":
                continue  # a placement transform: not a plain copy
            clsid = ole_clsid(po.body_raw)
            if not clsid or clsid in seen:
                continue
            seen.add(clsid)
            props = po.raw_properties()
            gh = G.parse_geom_header(po.body_raw)
            size = (0, 0)
            if gh is not None:
                l, t, r, b = G.norm_rect(gh.rect1)
                size = (r - l, b - t)
            out.append(
                ControlDonor(
                    clsid, path, i, props.get("ACTIVEXEXT_*NAME") or props.get("COMMENT") or "", size
                )
            )
    res = tuple(out)
    _DONOR_CACHE[project_dir] = (sig, res)
    return res


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("activex.donors", _DONOR_CACHE, on=("refresh", "tests"))
_caches.register("activex.control_info", _control_info, on=("refresh", "tests"))
_caches.register("activex.control_members", _control_members, on=("refresh", "tests"))
