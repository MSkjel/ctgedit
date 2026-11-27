"""Read a project's DBF tables for the editor (tag pickers, page metadata)."""

from __future__ import annotations

import bisect
import heapq
import os
import re
import threading
from array import array
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..format.cnum import is_digits
from ..format import dbfcols
from ..format.textcodec import CODEC
from . import tagscope as TS


def default_project() -> str:
    """The ACTIVE install's ``User`` Example project (the tree the live toolchain
    uses, ``citect_config.live_user_dir``; CTG_SCADA_ROOT = a fake install), read
    at each call; '' when the install has none."""
    try:
        from .citect_config import live_user_dir

        user = live_user_dir()
    except Exception:  # noqa: BLE001 -- no registry / unreadable ini
        user = None
    if user:
        path = os.path.join(user, "Example")
        if os.path.isdir(path):
            return path
    return ""


def __getattr__(name: str):
    # DEFAULT_PROJECT is read at each use (a test or a fake install may set
    # CTG_SCADA_ROOT after import)
    if name == "DEFAULT_PROJECT":
        return default_project()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


#: pages.DBF column widths per table (path -> ((path, mtime, size), widths))
_WIDTHS_CACHE: dict = {}


def _stamp_dbf_date(head: bytearray) -> None:
    """dBASE header bytes 1-3 = date of last update (YY since 1900, MM, DD),
    stamped by every dBASE writer."""
    from ..infra import identity

    d = identity.today()
    head[1], head[2], head[3] = d.year - 1900, d.month, d.day


@dataclass(frozen=True)
class VariableTag:
    name: str
    type: str = ""
    unit: str = ""
    address: str = ""
    eng_units: str = ""
    comment: str = ""
    cluster: str = ""
    equip: str = ""
    item: str = ""
    format: str = ""
    eng_zero: str = ""
    eng_full: str = ""


#: the variable.dbf columns a VariableTag is made of
_VARTAG_COLUMNS = (
    "NAME",
    "TYPE",
    "UNIT",
    "ADDR",
    "ENG_UNITS",
    "COMMENT",
    "CLUSTER",
    "EQUIP",
    "ITEM",
    "FORMAT",
    "ENG_ZERO",
    "ENG_FULL",
)


def _read_variable_tags(path: str) -> tuple[VariableTag, ...]:
    """The VariableTags of a variable.dbf, as dbfread reads the table: Citect DBFs
    never declare a code page (language-driver byte 0x00), so the ANSI codec is given (Plant SCADA writes the strings as raw ANSI,
    [CTEDIT] AnsiToOem=0) and undecodable bytes are replaced."""
    tags: list[VariableTag] = []
    for name, typ, unit, addr, eu, comment, cluster, equip, item, fmt, zero, full in dbfcols.dbfread_columns(
        path, _VARTAG_COLUMNS
    ):
        name = (name or "").strip()
        if not name:
            continue
        tags.append(
            VariableTag(
                name=name,
                type=(typ or "").strip(),
                unit=(unit or "").strip(),
                address=(addr or "").strip(),
                eng_units=(eu or "").strip(),
                comment=(comment or "").strip(),
                cluster=(cluster or "").strip(),
                equip=(equip or "").strip(),
                item=(item or "").strip(),
                format=(fmt or "").strip(),
                eng_zero=str(zero or "").strip(),
                eng_full=str(full or "").strip(),
            )
        )
    return tuple(tags)


@dataclass(frozen=True)
class Project:
    #: the project folder ('' = the active install's Example project, default_project)
    path: str = ""

    def __post_init__(self) -> None:
        if not self.path:
            object.__setattr__(self, "path", default_project())

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("\\/"))

    def _dbf_path(self, table: str) -> str | None:
        if not self.path:
            return None
        p = os.path.join(self.path, table)
        return p if os.path.exists(p) else None

    def _variable_dbf(self) -> str | None:
        return self._dbf_path("variable.dbf") or self._dbf_path("VARIABLE.DBF")

    def variable_tags(self) -> tuple[VariableTag, ...]:
        """The project's variable tags (variable.dbf rows with a NAME, in file
        order) -- dbfread's reading of the table, kept per file version for
        several projects at once (format.dbfcols)."""
        path = self._variable_dbf()
        if not path:
            return ()
        return dbfcols.cached(
            path, ("vartags",), _read_variable_tags, lambda v: len(v) * len(_VARTAG_COLUMNS)
        )

    def variable_formats(self) -> tuple[tuple[str, str], ...]:
        """(lower-case NAME, FORMAT) of every variable tag, in file order: what
        ``{t.name.lower(): t.format for t in variable_tags()}`` is built from,
        read from the two columns only (cached per file version)."""
        path = self._variable_dbf()
        if not path:
            return ()
        return dbfcols.cached(path, ("formats",), self._read_formats, lambda v: len(v) * 2)

    def _read_formats(self, path: str) -> tuple[tuple[str, str], ...]:
        rows = dbfcols.dbfread_columns(path, ("NAME", "FORMAT"), fallback=False)
        if rows is None:
            # a table dbfread reads itself: exactly what the tag list gives (and raises)
            return tuple((t.name.lower(), t.format) for t in self.variable_tags())
        out = []
        for name, fmt in rows:
            name = (name or "").strip()
            if name:
                out.append((name.lower(), (fmt or "").strip()))
        return tuple(out)

    def tag_names(self) -> list[str]:
        return [t.name for t in self.variable_tags()]

    def search_tags(self, query: str, limit: int = 50) -> list[VariableTag]:
        q = query.strip().lower()
        if not q:
            return list(self.variable_tags())[:limit]
        out = []
        for t in self.variable_tags():
            if q in t.name.lower() or q in t.comment.lower():
                out.append(t)
                if len(out) >= limit:
                    break
        return out

    # -- page records (pages.DBF) -------------------------------------------
    # Compiler-facing page metadata (CtDraw "Page Properties" dialog) in pages.DBF:
    # TITLE, COMMENT, AREA, CLUSTER, CINHERIT, SCANTIME, LOGDEVICE, PREV_PAGE/NEXT_PAGE,
    # PARENT, CON_TYPE, the event functions (ENTRY_FUNC/EXIT_FUNC/ACT_FUNC/DEACT_FUNC/
    # READY_FUNC/BGND_FUNC), IGNOREVQ. CtDraw maps each to a '!' page property
    # (!TITLE -> TITLE, !PDESC -> COMMENT, !PREV / !NEXT,
    # !AREA, !ENTRY / !ALWAYS / !EXIT / !ACTIVATE / !DEACTIVATE / !READY, !SCANTIME,
    # !LOGDEVICE, !CLUSTER, !CINHERIT, !IGNOREVQ, !CONTYPE, !PARENT) and writes the
    # row when the page is SAVED; EditableDocument stages the edits the same way.

    def _table_path(self, name: str) -> str | None:
        p = os.path.join(self.path, name)
        return p if os.path.exists(p) else None

    def page_rows(self) -> list[dict]:
        """All pages.DBF rows (char fields, stripped). [] when absent."""
        from ..format.projects import read_dbf_rows

        p = self._table_path("pages.DBF") or self._table_path("pages.dbf")
        return read_dbf_rows(p) if p else []

    def page_names(self) -> list[str]:
        return [r.get("NAME", "").strip() for r in self.page_rows() if r.get("NAME", "").strip()]

    # pages.DBF NAME is C(64) on every shipped table: a longer name is stored
    # truncated, so it must be matched truncated (else every save would append a
    # duplicate registration row)
    NAME_LEN = 64

    def page_record(self, page_name: str) -> dict | None:
        low = page_name.strip().lower()[: self.NAME_LEN]
        for r in self.page_rows():
            if r.get("NAME", "").strip().lower() == low:
                return r
        return None

    def update_page_record(self, page_name: str, updates: dict) -> bool:
        """Patch fields of one pages.DBF row in place."""
        p = self._table_path("pages.DBF") or self._table_path("pages.dbf")
        if not p or not updates:
            return False
        import struct as _struct
        from ..format import journal
        from ..format.textcodec import encode_field, truncate

        encoded = {
            str(k).upper(): encode_field(v, "the %s field of pages.DBF" % str(k).upper())
            for k, v in updates.items()
        }
        with journal.open_patch(p) as fh:
            head = fh.read(32)
            if len(head) < 32:
                return False
            nrec = _struct.unpack_from("<I", head, 4)[0]
            hdr_size = _struct.unpack_from("<H", head, 8)[0]
            rec_size = _struct.unpack_from("<H", head, 10)[0]
            fh.seek(32)
            desc = fh.read(hdr_size - 32)
            fields = {}
            off, pos = 0, 1
            while off + 32 <= len(desc) and desc[off] != 0x0D:
                fname = desc[off : off + 11].split(b"\x00")[0].decode(CODEC, "replace").strip().upper()
                ln = desc[off + 16]
                fields[fname] = (pos, ln)
                pos += ln
                off += 32
            if "NAME" not in fields:
                return False
            low = page_name.strip().lower()
            # (the LAST live row of the name, as the register and a Save As take it)
            for i in range(nrec - 1, -1, -1):
                base = hdr_size + i * rec_size
                fh.seek(base)
                rec = fh.read(rec_size)
                if len(rec) < rec_size or rec[0] == 0x2A:
                    continue
                noff, nln = fields["NAME"]
                if rec[noff : noff + nln].decode(CODEC, "replace").strip().lower() != low:
                    continue
                for key, raw in encoded.items():
                    f = fields.get(key)
                    if f is None:
                        continue
                    foff, fln = f
                    enc = truncate(raw, fln).ljust(fln)
                    fh.seek(base + foff)
                    fh.write(enc)
                head = bytearray(head)
                _stamp_dbf_date(head)
                fh.seek(0)
                fh.write(bytes(head))
                return True
        return False

    def add_page_record(self, page_name: str, fields: dict | None = None) -> bool:
        """Register a new page in pages.DBF."""
        p = self._table_path("pages.DBF") or self._table_path("pages.dbf")
        if not p or self.page_record(page_name) is not None:
            return False
        import struct as _struct
        from ..format import journal
        from ..format.textcodec import encode_field, truncate

        # CtDraw's own registration of a new page writes TITLE = NAME and
        # leaves IGNOREVQ / CINHERIT blank
        values = {"NAME": page_name, "TITLE": page_name}
        for k, v in (fields or {}).items():
            values[str(k).upper()] = str(v)
        encoded = {k: encode_field(v, "the %s field of pages.DBF" % k) for k, v in values.items()}
        with journal.open_patch(p) as fh:
            head = bytearray(fh.read(32))
            if len(head) < 32:
                return False
            nrec = _struct.unpack_from("<I", head, 4)[0]
            hdr_size = _struct.unpack_from("<H", head, 8)[0]
            rec_size = _struct.unpack_from("<H", head, 10)[0]
            fh.seek(32)
            desc = fh.read(hdr_size - 32)
            fmap = {}
            off, pos = 0, 1
            while off + 32 <= len(desc) and desc[off] != 0x0D:
                fname = desc[off : off + 11].split(b"\x00")[0].decode(CODEC, "replace").strip().upper()
                fmap[fname] = (pos, desc[off + 16])
                pos += desc[off + 16]
                off += 32
            if "NAME" not in fmap or pos > rec_size:
                return False
            rec = bytearray(b" " * rec_size)
            for k, raw in encoded.items():
                f = fmap.get(k)
                if f is None:
                    continue
                foff, fln = f
                rec[foff : foff + fln] = truncate(raw, fln).ljust(fln)
            fh.seek(hdr_size + nrec * rec_size)
            fh.write(bytes(rec) + b"\x1a")
            _struct.pack_into("<I", head, 4, nrec + 1)
            _stamp_dbf_date(head)
            fh.seek(0)
            fh.write(bytes(head))
        return True

    def copy_page_record(self, src_dir: str, src_name: str, new_name: str) -> bool:
        """Save As: register ``new_name`` with a copy of the source page's row."""
        import struct as _struct
        from ..format.dbf import Dbf

        src_p = Project(src_dir)._pages_path()
        dst_p = self._pages_path()
        if not src_p or not dst_p:
            return False
        try:
            with Dbf(src_p, writable=False) as src:
                live = [r for _i, r in src.find("NAME", src_name) if r[:1] != b"*"]
                if not live:
                    return False
                row = src.decode(live[-1])  # the row register_page reads
            dst = Dbf(dst_p)
        except ValueError, KeyError, _struct.error:  # not a usable pages table
            return False
        row = {k: v for k, v in row.items() if k.upper() != "NAME"}
        row["NAME"] = new_name
        with dst:
            if "NAME" not in dst.fmap:
                return False
            rec = dst.encode(row)
            cur = [i for i, r in dst.find("NAME", new_name) if r[:1] != b"*"]
            if cur:
                if dst.read(cur[-1]) == rec:
                    return True
                dst.write(cur[-1], rec)
            else:
                dst.append([rec])
            dst.touch()
        return True

    def delete_page_record(self, page_name: str) -> list[int]:
        """Flag every live pages.DBF row of ``page_name`` deleted ('*'; the
        compiler and Studio skip it). Returns their 1-based record numbers."""
        from ..format.dbf import Dbf

        p = self._pages_path()
        if not p:
            return []
        with Dbf(p) as d:
            rows = [i for i, r in d.find("NAME", page_name) if r[:1] != b"*"]
            for i in rows:
                d.delete(i)
            if rows:
                d.touch()
        return [i + 1 for i in rows]

    def rename_page_records(self, renames: dict) -> dict:
        """Give the pages.DBF rows of pages ``renames`` ({old lower case: new}) their new names
        (a TITLE equal to the old name too). Returns {new: 1-based record numbers}."""
        from ..format.dbf import Dbf

        p = self._pages_path()
        if not p:
            return {}
        out: dict = {}
        with Dbf(p) as d:
            for i in range(d.nrec):
                r = d.read(i)
                if r[:1] == b"*":
                    continue
                old = d.field(r, "NAME").strip()
                new = renames.get(old.lower())
                if new is None:
                    continue
                row = d.decode(r)
                row["NAME"] = new
                if row.get("TITLE", "").strip().lower() == old.lower():
                    row["TITLE"] = new
                d.write(i, d.encode(row))
                out.setdefault(new, []).append(i + 1)
            if out:
                d.touch()
        return out

    def rename_page_links(self, renames: dict) -> list[tuple]:
        """Point Previous / Next / Parent at the new names of ``renames`` ({old lower case:
        new}); returns (page, column) of each."""
        p = self._pages_path()
        if not p:
            return []
        return [(row.get("NAME", "").strip(), c) for row, c in _rename_values(p, PAGE_LINK_COLUMNS, renames)]

    def rename_page_names(self, renames: dict) -> list[tuple]:
        """Give the equipment, menu, alarm help and [Page] parameter entries naming a page of
        ``renames`` ({old lower case: new}) its new name; returns (table, column) of each."""
        out = []
        for table, cols in PAGE_NAME_COLUMNS.items():
            p = self._table_path(table)
            if p:
                out += [(table, c) for _row, c in _rename_values(p, cols, renames)]
        p = self._table_path("param.dbf")
        if p:
            keys = {k.lower() for k in PAGE_PARAMETERS}

            def page_parameter(row: dict) -> bool:
                return (
                    row.get("SECTION", "").strip().lower() == "page"
                    and row.get("NAME", "").strip().lower() in keys
                )

            out += [("param.dbf", c) for _row, c in _rename_values(p, ("DATA",), renames, page_parameter)]
        return out

    def _pages_path(self) -> str | None:
        return self._table_path("pages.DBF") or self._table_path("pages.dbf")

    def has_page_table(self) -> bool:
        """The folder is a project that registers pages (it has pages.DBF)."""
        return self._pages_path() is not None

    def page_field_widths(self) -> dict:
        """{column: width} of the project's pages.DBF (TITLE 64, COMMENT
        250, AREA 16, CLUSTER 16, CON_TYPE 20, the event columns 254 on every
        shipped table); {} when the project has none or it is unreadable."""
        import struct as _struct
        from ..format.dbf import Dbf

        p = self._pages_path()
        if not p:
            return {}
        try:
            st = os.stat(p)
            key = (os.path.normcase(os.path.abspath(p)), st.st_mtime_ns, st.st_size)
            hit = _WIDTHS_CACHE.get(key[0])
            if hit is not None and hit[0] == key:
                return dict(hit[1])
            with Dbf(p, writable=False) as d:
                widths = {n.upper(): ln for n, _o, ln in d.fields}
        except OSError, ValueError, _struct.error:
            return {}
        _WIDTHS_CACHE[key[0]] = (key, widths)
        return dict(widths)

    def page_keyboard_rows(self, page_name: str, page_wide: bool = False) -> list[dict]:
        """pgkey.DBF rows for one page (compiled keyboard commands);
        ``page_wide`` = only the page-wide commands (blank AN)."""
        from ..format.projects import read_dbf_rows

        p = self._table_path("pgkey.DBF") or self._table_path("pgkey.dbf")
        if not p:
            return []
        low = page_name.strip().lower()
        return [
            r
            for r in read_dbf_rows(p)
            if r.get("PAGE", "").strip().lower() == low and not (page_wide and r.get("AN", "").strip())
        ]

    def copy_page_keyboard_rows(self, src_dir: str, src_name: str, new_name: str) -> int:
        """Save As: the source page's page-wide keyboard commands (blank-AN
        pgkey rows -- not stored in the .ctg) become ``new_name``'s here.
        Returns the number of rows the new page has."""
        from . import pgregister

        rows = pgregister.page_keyboard_rows(src_dir, src_name)
        if not rows and not pgregister.page_keyboard_rows(self.path, new_name):
            return 0
        return pgregister.set_page_keyboard_rows(self.path, new_name, rows)


def delete_page(project_dir: str, page_name: str, *, files: bool = True) -> dict:
    """Delete a page, as the Graphics Builder's Open dialog does."""
    from . import pgregister
    from ..format import journal, projectlock

    name = (
        os.path.splitext(os.path.basename(page_name))[0] if page_name.lower().endswith(".ctg") else page_name
    )
    rep: dict = {"page": name, "records": [], "tables": {}, "files": [], "errors": [], "compstate": None}
    proj = Project(project_dir)
    with projectlock.page(os.path.join(project_dir, name + ".ctg")), projectlock.project(project_dir):
        with journal.transaction("delete page %s" % name):
            rep["records"] = proj.delete_page_record(name)
            rep["tables"] = pgregister.delete_page_rows(project_dir, name)
            if rep["records"] or rep["tables"]:
                rep["compstate"] = pgregister.mark_uncompiled(project_dir)
        # the page has no rows any more, and no Update Pages run visits it again: a rows
        # pending marker an earlier save left goes too (io/pendingrows)
        from . import pendingrows

        pendingrows.clear(os.path.join(project_dir, name + ".ctg"))
        exts = (".rdb",) + ((".ctg", ".ctF", ".ctf") if files else ())
        seen = set()
        for ext in exts:
            p = pgregister._find_file(project_dir, name + ext)
            if p is None or os.path.normcase(p) in seen:
                continue
            seen.add(os.path.normcase(p))
            try:
                os.remove(p)
                rep["files"].append(p)
            except OSError as e:
                rep["errors"].append("%s: %s" % (os.path.basename(p), e.strerror or e))
    return rep


#: the pages.DBF columns that name another page (Previous, Next, Parent)
PAGE_LINK_COLUMNS = ("PREV_PAGE", "NEXT_PAGE", "PARENT")

#: the columns of other tables that name a page: equipment, menus, alarm help pages
PAGE_NAME_COLUMNS = {
    "equip.dbf": ("PAGE", "HELP"),
    "pagemenu.dbf": ("PAGE", "TARGETPAGE"),
    **{
        t + ".dbf": ("HELP",)
        for t in ("digalm", "anaalm", "advalm", "argdig", "argana", "hresalm", "tsdig", "tsana", "dbptalm")
    },
}
#: the [Page] parameters that name a page
PAGE_PARAMETERS = ("Startup", "HomePage", "Splash")


def _rename_values(path: str, cols, renames: dict, where=None) -> list[tuple]:
    """Set the columns ``cols`` of table ``path`` that hold an old name of ``renames`` ({old
    lower case: new}) to its new name, in the rows ``where(row)`` allows; returns (row, column)
    of each."""
    from ..format.dbf import Dbf

    out = []
    with Dbf(path) as d:
        cols = [c for c in cols if c in d.fmap]
        if not cols:
            return []
        for i in range(d.nrec):
            r = d.read(i)
            if r[:1] == b"*":
                continue
            hit = [c for c in cols if d.field(r, c).strip().lower() in renames]
            if not hit:
                continue
            row = d.decode(r)
            if where is not None and not where(row):
                continue
            for c in hit:
                row[c] = renames[row[c].strip().lower()]
                out.append((row, c))
            d.write(i, d.encode(row))
        if out:
            d.touch()
    return out


class RenameStopped(Exception):
    """Stop was asked for: nothing was renamed."""


def rename_pages(
    project_dir: str,
    pairs: Sequence[tuple],
    *,
    links: bool = True,
    names: bool = True,
    link_dirs: Sequence[str] = (),
    progress=None,
) -> dict:
    """Rename pages ``pairs`` [(old, new)] of ``project_dir`` in one go: their .ctg and .ctF,
    pages.DBF rows and page table rows; with ``links`` the Previous / Next / Parent naming them,
    with ``names`` the equipment, menu, alarm help and [Page] parameter entries
    (Project.rename_page_names), in the project and in ``link_dirs``. Names may swap. A failure,
    or ``progress(i, n, step)`` returning False (RenameStopped), changes nothing."""
    import contextlib

    from . import pgregister, pendingrows
    from ..format import journal, projectlock

    rep: dict = {"renamed": [], "records": {}, "tables": {}, "links": [], "names": [], "errors": []}
    rep["compstate"] = None
    spelt = []
    for old, new in pairs:
        f = pgregister.find_file(project_dir, old + ".ctg")
        if f is None:
            raise FileNotFoundError("page %s is not in %s" % (old, project_dir))
        old = os.path.splitext(os.path.basename(f))[0]  # (as the file spells it)
        if new != old:
            spelt.append((old, new))
    if not spelt:
        return rep
    project = os.path.basename(os.path.normpath(project_dir))
    leaving = {o.lower() for o, _n in spelt}
    if len(leaving) < len(spelt) or len({n.lower() for _o, n in spelt}) < len(spelt):
        raise ValueError("two pages of %s are given the same name" % project)
    renames = {o.lower(): n for o, n in spelt}
    here = os.path.normcase(os.path.abspath(project_dir))
    others = [d for d in link_dirs if os.path.normcase(os.path.abspath(d)) != here]
    steps = ["page files", "pages", "page tables"] + [
        os.path.basename(os.path.normpath(d)) for d in [project_dir] + others
    ]

    def step(k: int) -> None:
        if progress is not None and progress(k, len(steps), steps[k]) is False:
            raise RenameStopped()

    proj = Project(project_dir)
    with contextlib.ExitStack() as locks:
        for o, n in spelt:
            locks.enter_context(projectlock.page(os.path.join(project_dir, o + ".ctg")))
            locks.enter_context(projectlock.page(os.path.join(project_dir, n + ".ctg")))
        locks.enter_context(projectlock.project(project_dir))
        for _o, n in spelt:
            if n.lower() not in leaving and (
                pgregister.find_file(project_dir, n + ".ctg") is not None or proj.page_record(n) is not None
            ):
                raise FileExistsError("project %s already has a page %s" % (project, n))
        # every file goes to a spare name first, then to its own: a swap needs no third page
        moves = []
        for k, (o, n) in enumerate(spelt):
            for ext in (".ctg", ".ctF"):
                src = pgregister.find_file(project_dir, o + ext)
                if src is not None:
                    spare = os.path.join(project_dir, "~ctgedit-rename-%d%s" % (k, os.path.splitext(src)[1]))
                    moves.append((src, spare, os.path.join(project_dir, n + os.path.splitext(src)[1])))
        done = []
        try:
            step(0)
            for src, spare, _dst in moves:
                if os.path.exists(spare):
                    raise FileExistsError(spare)
                os.rename(src, spare)
                done.append((src, spare))
            for _src, spare, dst in moves:
                os.rename(spare, dst)
                done.append((spare, dst))
            with journal.transaction("rename pages of %s" % project):
                step(1)
                rep["records"] = proj.rename_page_records(renames)
                step(2)
                rep["tables"] = pgregister.rename_page_rows(project_dir, renames)
                for k, d in enumerate([project_dir] + others):
                    step(3 + k)
                    other = d is not project_dir
                    with projectlock.project(d) if other else contextlib.nullcontext():
                        p = Project(d) if other else proj
                        got = p.rename_page_links(renames) if links else []
                        named = p.rename_page_names(renames) if names else []
                    tag = os.path.basename(os.path.normpath(d)) + "\\" if other else ""
                    rep["links"] += [(tag + n, c) for n, c in got]
                    rep["names"] += [(tag + t, c) for t, c in named]
                    if other and (got or named):
                        pgregister.mark_uncompiled(d)
                if rep["records"] and rep["tables"]:
                    pgregister.log_changes(
                        project_dir,
                        [
                            {"NAME": "pages", "KEY": n, "RECORD_NO": str(recs[-1])}
                            for n, recs in rep["records"].items()
                        ],
                    )
                if rep["records"] or rep["tables"] or rep["links"] or rep["names"]:
                    rep["compstate"] = pgregister.mark_uncompiled(project_dir)
        except BaseException:
            for a, b in reversed(done):
                with contextlib.suppress(OSError):  # swallow-ok: put back as far as it goes
                    os.rename(b, a)
            raise
        rep["renamed"] = spelt
        for o, n in spelt:
            old_ctg, new_ctg = os.path.join(project_dir, o + ".ctg"), os.path.join(project_dir, n + ".ctg")
            # a pending-rows marker moves with the page
            if pendingrows.marked(old_ctg) is not None:
                pendingrows.clear(old_ctg)
                pendingrows.mark(new_ctg)
            rdb = pgregister.find_file(project_dir, o + ".rdb")
            if rdb is not None:
                try:
                    os.remove(rdb)
                except OSError as e:
                    rep["errors"].append("%s: %s" % (os.path.basename(rdb), e.strerror or e))
    return rep


# -- names an expression can use, across the tag scope -------------------------
# CtDraw's Insert Tag lists the variable tags of the page's project AND of
# every project it includes (the compiler resolves a tag through the same
# chain), plus the Equip.Item references (CtDraw's 'Equip.Item' / 'EQUIP.ITEM',
# help 1172161: "Insert Tag ... Equipment.item references"). Local variables
# (locvar.dbf) are tags too: SA_Library's SA_DisableLabel gates every SA genie.
# ctgedit also lists the projects compiled with the page's (io.tagscope): a page
# of a library project uses the tags of the projects that include it.


@dataclass(frozen=True)
class TagEntry:
    """A name an expression can use: a variable tag, a local variable or an
    equipment item reference (``Equipment.Item``). ``equip`` / ``item`` = the
    tag's EQUIP / ITEM (variable tags and equipment items)."""

    name: str
    kind: str  # 'variable' | 'local' | 'equipment'
    project: str
    type: str = ""
    comment: str = ""
    eng_zero: str = ""
    eng_full: str = ""
    eng_units: str = ""
    equip: str = ""
    item: str = ""


@dataclass(frozen=True)
class TrendTag:
    """A trend tag (trend.dbf) of the tag scope."""

    name: str
    project: str
    expr: str = ""
    comment: str = ""
    type: str = ""
    eng_units: str = ""


def _find_table(folder: str, name: str) -> str | None:
    return TS.find_table(folder, name)


def _cached_rows(path: str) -> list:
    """read_dbf_rows, kept until the file changes (format.dbfcols; the rows are
    shared: read-only)."""
    if not os.path.exists(path):
        return []
    return dbfcols.rows(path)


#: what the expression index reads of a variable.dbf / locvar.dbf row
_INDEX_VARIABLE = ("NAME", "TYPE", "COMMENT", "ENG_ZERO", "ENG_FULL", "ENG_UNITS", "EQUIP", "ITEM")
_INDEX_LOCAL = ("NAME", "TYPE", "COMMENT", "ZERO", "FULL", "ENG_UNITS")
#: the variable.dbf columns kept for a scope (the index + the display FORMAT)
TAG_COLUMNS = _INDEX_VARIABLE + ("FORMAT",)
#: the equip.dbf columns the equipment lists read
EQUIP_COLUMNS = ("NAME", "TYPE", "COMMENT", "PAGE", "CLUSTER", "CONTENT")
#: the trend.dbf columns the trend lists read
TREND_COLUMNS = ("NAME", "EXPR", "COMMENT", "TYPE", "ENG_UNITS")


def chain_dirs(project_dir: str) -> list[str]:
    """The project and its include chain in CtDraw's search order (the
    project itself first; format.projects.include_search_dirs). Only the
    projects it includes: :func:`scope_dirs` adds the projects compiled with it."""
    from ..format.projects import include_search_dirs

    project_dir = os.path.abspath(project_dir)
    try:
        return [project_dir] + include_search_dirs(project_dir)
    except Exception:  # noqa: BLE001 -- a broken registry still has the project
        return [project_dir]


def scope_of(project_dir: str) -> TS.TagScope:
    """The tag scope of a page folder (io.tagscope.scope)."""
    return TS.scope(project_dir)


def scope_dirs(project_dir: str) -> list[str]:
    """The folders of the page folder's tag scope, in scope order."""
    return TS.scope(project_dir).dirs()


# -- per-table structures (kept with the column table, shared by every scope) --
def _named_rows(t) -> array:
    """The rows of a table with a NAME, sorted by the lower-case name (stable)."""

    def build(t):
        names = t.column("NAME")
        rows = [r for r in range(len(names)) if names[r]]
        rows.sort(key=lambda r: names[r].lower())
        return array("I", rows)

    return t.derived("name_order", build, lambda a: 4 * len(a))


def _name_text(t):
    """(the lower-case names of :func:`_named_rows` joined by line breaks, the
    start of each), for a contains search in one pass."""

    def build(t):
        names, order = t.column("NAME"), _named_rows(t)
        low = [names[r].lower() for r in order]
        starts = array("I")
        pos = 0
        for s in low:
            starts.append(pos)
            pos += len(s) + 1
        return "\n".join(low), starts

    return t.derived("name_text", build, lambda v: 49 + len(v[0]) + 4 * len(v[1]))


def _item_key(eq: str, it: str) -> str:
    return (eq + "." + it).lower()


def _item_rows(t) -> array:
    """The rows with an EQUIP and an ITEM, sorted by the lower-case 'Equip.Item'."""

    def build(t):
        eq, it = t.column("EQUIP"), t.column("ITEM")
        names = t.column("NAME")
        rows = [r for r in range(len(eq)) if eq[r] and it[r] and names[r]]
        rows.sort(key=lambda r: _item_key(eq[r], it[r]))
        return array("I", rows)

    return t.derived("item_order", build, lambda a: 4 * len(a))


def _item_text(t):
    def build(t):
        eq, it, order = t.column("EQUIP"), t.column("ITEM"), _item_rows(t)
        low = [_item_key(eq[r], it[r]) for r in order]
        starts = array("I")
        pos = 0
        for s in low:
            starts.append(pos)
            pos += len(s) + 1
        return "\n".join(low), starts

    return t.derived("item_text", build, lambda v: 49 + len(v[0]) + 4 * len(v[1]))


def _equipment_names(t):
    """(sorted distinct lower-case EQUIP values, their set)."""

    def build(t):
        vals = sorted({e.lower() for e in t.column("EQUIP") if e})
        return vals, frozenset(vals)

    return t.derived("equip_names", build, lambda v: 80 * len(v[0]))


def _distinct(t, column: str) -> tuple:
    """The distinct values of a column (its values are shared objects)."""
    return t.derived(
        ("distinct", column),
        lambda t: tuple({id(v): v for v in t.column(column)}.values()),
        lambda v: 8 * len(v),
    )


def _find_row(t, low: str) -> Optional[int]:
    """The first row whose NAME is ``low`` (lower case), or None."""
    order = _named_rows(t)
    names = t.column("NAME")
    i = bisect.bisect_left(order, low, key=lambda r: names[r].lower())
    if i < len(order) and names[order[i]].lower() == low:
        return order[i]
    return None


def _find_item_row(t, low: str) -> Optional[int]:
    order = _item_rows(t)
    eq, it = t.column("EQUIP"), t.column("ITEM")
    i = bisect.bisect_left(order, low, key=lambda r: _item_key(eq[r], it[r]))
    if i < len(order) and _item_key(eq[order[i]], it[order[i]]) == low:
        return order[i]
    return None


def _text_hits(text: str, starts, q, rows) -> List[int]:
    """The rows (``rows[k]`` for each line k of ``text``) whose line contains ``q`` (a text,
    or a compiled regular expression: ^ and $ anchor each line, re.M; a match is checked
    on its own line, so one that ran over a line break does not count)."""
    out = []
    n = len(starts)
    if not isinstance(q, str):
        m = q.search(text)
        while m is not None:
            k = bisect.bisect_right(starts, m.start()) - 1
            nxt = starts[k + 1] if k + 1 < n else len(text) + 1
            line = text[starts[k] : nxt - 1]
            if q.search(line) is not None:
                out.append(rows[k])
            m = q.search(text, nxt) if nxt <= len(text) else None
        return out
    if len(q) <= 2:
        # a short text hits most lines: one substring test per line instead
        return [rows[k] for k, line in enumerate(text.split("\n")) if q in line]
    pos = text.find(q)
    while pos >= 0:
        k = bisect.bisect_right(starts, pos) - 1
        out.append(rows[k])
        nxt = starts[k + 1] if k + 1 < n else len(text)
        pos = text.find(q, nxt)
    return out


_SHIFT = 24
_ROWMASK = (1 << _SHIFT) - 1
#: TagIndex.filter tests each code on its own when there are at most this many, or this
#: share of the scope's rows (narrowing as the user types on), else it scans the tables
_PER_CODE_MIN = 20000
_PER_CODE_SHARE = 8
#: a completion searches inside the Equip.Item references of tables up to this size
_ITEM_TEXT_ROWS = 20_000


class _Source:
    """One table of a tag index: a project's variable tags or local variables."""

    __slots__ = (
        "project",
        "relation",
        "kind",
        "table",
        "name",
        "type",
        "comment",
        "zero",
        "full",
        "units",
        "equip",
        "item",
        "format",
    )

    def __init__(self, project: str, relation: str, kind: str, table):
        self.project = project
        self.relation = relation
        self.kind = kind
        self.table = table
        c = table.column
        self.name = c("NAME")
        self.type = c("TYPE")
        self.comment = c("COMMENT")
        local = kind == "local"
        self.zero = c("ZERO" if local else "ENG_ZERO")
        self.full = c("FULL" if local else "ENG_FULL")
        self.units = c("ENG_UNITS")
        blank = ("",) * len(self.name)
        self.equip = blank if local else c("EQUIP")
        self.item = blank if local else c("ITEM")
        self.format = blank if local else c("FORMAT")

    def entry(self, r: int) -> TagEntry:
        return TagEntry(
            self.name[r],
            self.kind,
            self.project,
            self.type[r],
            self.comment[r],
            self.zero[r],
            self.full[r],
            self.units[r],
            self.equip[r],
            self.item[r],
        )

    def item_entry(self, r: int) -> TagEntry:
        eq, it = self.equip[r], self.item[r]
        return TagEntry(
            "%s.%s" % (eq, it),
            "equipment",
            self.project,
            self.type[r],
            "Equipment item (tag %s)" % self.name[r],
            self.zero[r],
            self.full[r],
            self.units[r],
            eq,
            it,
        )


def _static_sources(entries) -> List[_Source]:
    """Sources made from a list of TagEntry (a TagIndex built in memory)."""
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for e in entries:
        groups.setdefault((e.project, e.kind), []).append(e)
    out = []
    for (project, kind), es in groups.items():
        cols = ("NAME", "TYPE", "COMMENT", "ENG_ZERO", "ENG_FULL", "ENG_UNITS", "EQUIP", "ITEM", "FORMAT")
        vals = [
            [e.name for e in es],
            [e.type for e in es],
            [e.comment for e in es],
            [e.eng_zero for e in es],
            [e.eng_full for e in es],
            [e.eng_units for e in es],
            [e.equip for e in es],
            [e.item for e in es],
            [""] * len(es),
        ]
        t = dbfcols.ColumnTable.from_columns(cols, vals)
        src = _Source(project, "", "variable" if kind == "equipment" else kind, t)
        src.kind = kind
        out.append(src)
    return out


class TagIndex:
    """The expression names of a tag scope."""

    KINDS = ("variable", "local", "equipment")

    def __init__(self, entries=(), sources=None, scope=None):
        self._src: List[_Source] = list(sources) if sources is not None else _static_sources(entries)
        self.scope = scope
        self._orders: dict = {}
        self._lock = threading.Lock()
        self._len = None
        self._entries = None

    # -- the tables ---------------------------------------------------------------
    @property
    def sources(self) -> List[_Source]:
        return list(self._src)

    def projects(self) -> List[str]:
        """The projects that have tags, in scope order (once each)."""
        out = []
        for s in self._src:
            if s.project not in out:
                out.append(s.project)
        return out

    def _item_sources(self):
        """(index, source) of the sources whose rows give Equip.Item references."""
        return [(k, s) for k, s in enumerate(self._src) if s.kind == "variable"]

    def __len__(self) -> int:
        if self._len is None:
            n = 0
            for s in self._src:
                n += len(_named_rows(s.table))
                if s.kind == "variable":
                    n += len(_item_rows(s.table))
            self._len = n
        return self._len

    def empty(self) -> bool:
        return not any(len(_named_rows(s.table)) for s in self._src)

    # -- codes: one int per row of an order / search result -------------------------
    @staticmethod
    def code(source: int, row: int, item: bool = False) -> int:
        c = (source << _SHIFT) | row
        return -(c + 1) if item else c

    def entry(self, code: int) -> TagEntry:
        """The TagEntry of a code of :meth:`order` / :meth:`search`."""
        if code < 0:
            c = -code - 1
            return self._src[c >> _SHIFT].item_entry(c & _ROWMASK)
        return self._src[code >> _SHIFT].entry(code & _ROWMASK)

    def cells(self, code: int) -> tuple:
        """(name, kind, type, equip, project, comment) of a code, without a TagEntry."""
        if code < 0:
            c = -code - 1
            s, r = self._src[c >> _SHIFT], c & _ROWMASK
            return (
                "%s.%s" % (s.equip[r], s.item[r]),
                "equipment",
                s.type[r],
                s.equip[r],
                s.project,
                "Equipment item (tag %s)" % s.name[r],
            )
        s, r = self._src[code >> _SHIFT], code & _ROWMASK
        return (s.name[r], s.kind, s.type[r], s.equip[r], s.project, s.comment[r])

    def name_of(self, code: int) -> str:
        if code < 0:
            c = -code - 1
            s, r = self._src[c >> _SHIFT], c & _ROWMASK
            return "%s.%s" % (s.equip[r], s.item[r])
        return self._src[code >> _SHIFT].name[code & _ROWMASK]

    def project_of(self, code: int) -> str:
        c = -code - 1 if code < 0 else code
        return self._src[c >> _SHIFT].project

    # -- whole lists ----------------------------------------------------------------
    def _iters(self, kinds, project: Optional[str] = None):
        def names_of(names, base, rank, order):
            for r in order:
                yield names[r].lower(), rank, base | r

        def items_of(eq, it, base, rank, order):
            for r in order:
                yield _item_key(eq[r], it[r]), rank, -((base | r) + 1)

        its = []
        want = (project or "").lower()
        for k, s in enumerate(self._src):
            if want and s.project.lower() != want:
                continue
            if s.kind in kinds:
                its.append(names_of(s.name, k << _SHIFT, 2 * k, _named_rows(s.table)))
            if "equipment" in kinds and s.kind == "variable":
                its.append(items_of(s.equip, s.item, k << _SHIFT, 2 * k + 1, _item_rows(s.table)))
        return its

    def order(self, kinds: Sequence[str] = KINDS) -> array:
        """The codes of every name of ``kinds`` sorted by name (case-insensitive),
        a name defined twice listed once (its first definition). Kept."""
        key = tuple(sorted(set(kinds)))
        hit = self._orders.get(key)
        if hit is not None:
            return hit
        with self._lock:
            hit = self._orders.get(key)
            if hit is not None:
                return hit
            out = array("q")
            prev = None
            append = out.append
            breathe, every = dbfcols.breathe, dbfcols.YIELD_ROWS
            for n, (low, _rank, code) in enumerate(heapq.merge(*self._iters(key))):
                if not n % every:
                    breathe(True)
                if low != prev:
                    prev = low
                    append(code)
            self._orders[key] = out
            return out

    def sorted_names(self, kinds: Sequence[str] = ("variable",)) -> List[str]:
        """The names of :meth:`order` (kept: a list the callers share, read-only)."""
        key = ("names",) + tuple(sorted(set(kinds)))
        hit = self._orders.get(key)
        if hit is None:
            name_of = self.name_of
            hit = [name_of(c) for c in self.order(kinds)]
            with self._lock:
                hit = self._orders.setdefault(key, hit)
        return hit

    def names(self) -> list[str]:
        """Every name (variable tags, local variables, then the Equip.Item
        references), in scope and file order."""
        return [e.name for e in self.entries]

    @property
    def entries(self) -> tuple:
        """Every TagEntry: per project its variable tags and local variables in
        file order, then the Equip.Item references (the list is built on first
        use: a large scope has hundreds of thousands)."""
        if self._entries is None:
            out = []
            for s in self._src:
                if s.kind == "equipment":
                    continue
                for r in range(len(s.name)):
                    if s.name[r]:
                        out.append(s.entry(r))
            for s in self._src:
                if s.kind == "equipment":
                    out.extend(s.entry(r) for r in range(len(s.name)) if s.name[r])
                elif s.kind == "variable":
                    for r in range(len(s.name)):
                        if s.name[r] and s.equip[r] and s.item[r]:
                            out.append(s.item_entry(r))
            self._entries = tuple(out)
        return self._entries

    # -- lookups --------------------------------------------------------------------
    def find(self, name: str) -> Optional[int]:
        """The code of ``name`` (a tag, local variable or Equip.Item reference;
        case-insensitive), None when the scope does not define it."""
        low = (name or "").strip().lower()
        if not low:
            return None
        for k, s in enumerate(self._src):
            r = _find_row(s.table, low)
            if r is not None:
                return self.code(k, r)
        if "." in low:
            for k, s in self._item_sources():
                r = _find_item_row(s.table, low)
                if r is not None:
                    return self.code(k, r, item=True)
        return None

    def get(self, name: str) -> TagEntry | None:
        c = self.find(name)
        return self.entry(c) if c is not None else None

    def _is_equipment(self, low: str) -> bool:
        for _k, s in self._item_sources():
            if low in _equipment_names(s.table)[1]:
                return True
        return False

    def _equipment_below(self, low: str) -> bool:
        """An equipment name starts with ``low`` + '.' (``low`` is a level of the hierarchy)."""
        pre = low + "."
        for _k, s in self._item_sources():
            vals = _equipment_names(s.table)[0]
            i = bisect.bisect_left(vals, pre)
            if i < len(vals) and vals[i].startswith(pre):
                return True
        return False

    def equipment_children(self, prefix: str, limit: int = 500) -> List[Tuple[str, str]]:
        """(name, 'equipment' or 'item') of the level below the equipment ``prefix`` (it ends
        in '.'): 'Area.Pump1.' gives 'Area.Pump1.Motor' (equipment) and 'Area.Pump1.Speed'
        (an item), sorted, as the tables spell them; [] when no equipment starts so."""
        low = (prefix or "").lower()
        if not low.endswith(".") or len(low) < 2:
            return []
        n = len(low)
        found: Dict[str, Tuple[str, str]] = {}
        for _k, s in self._item_sources():
            order = _item_rows(s.table)
            eq, it = s.equip, s.item

            def keyf(r, eq=eq, it=it):
                return _item_key(eq[r], it[r])

            i = bisect.bisect_left(order, low, key=keyf)
            while i < len(order) and len(found) < limit:
                r = order[i]
                key = keyf(r)
                if not key.startswith(low):
                    break
                rest = key[n:]
                seg = rest.split(".", 1)[0]
                full = (eq[r] + "." + it[r])[: n + len(seg)]
                if "." in rest:
                    found.setdefault(full.lower(), (full, "equipment"))
                    # (past every name below it: '/' sorts just after '.')
                    i = bisect.bisect_left(order, low + seg + "/", lo=i + 1, key=keyf)
                else:
                    found.setdefault(full.lower(), (full, "item"))
                    i += 1
        return [found[k] for k in sorted(found)]

    def is_name(self, name: str) -> bool | None:
        """True when ``name`` (or its tag before a '.field' extension, or an
        equipment it names) is known; None when the index is empty."""
        if self.empty():
            return None
        low = (name or "").strip().lower()
        if self.find(low) is not None or self._is_equipment(low):
            return True
        head = low.split(".", 1)[0]
        if head != low and self.find(head) is not None:
            return True
        if self._equipment_below(low):
            return True
        pos = low.find(".")
        while pos > 0:
            if self._is_equipment(low[:pos]):
                return True
            pos = low.find(".", pos + 1)
        return False

    def eng_range(self, name: str):
        """(ENG_ZERO, ENG_FULL) of a variable tag / local variable (ZERO /
        FULL) -- or of the tag an ``Equipment.Item`` reference resolves to --
        None when ``name`` is none of these."""
        e = self.get(name)
        if e is None and "." in (name or ""):
            e = self.get(name.split(".", 1)[0])  # a tag extension: Tag.Field / Tag.V
            if e is not None and e.kind == "equipment":
                e = None
        if e is None:
            return None
        return e.eng_zero, e.eng_full

    # -- search / filter --------------------------------------------------------------
    def search(
        self, text: str, limit: int = 500, kinds: Sequence[str] = KINDS, project: Optional[str] = None
    ) -> List[int]:
        """Up to ``limit`` codes whose name contains ``text`` (case-insensitive):
        the names that START with it first, both parts sorted by name."""
        low = (text or "").strip().lower()
        if not low or limit <= 0:
            return []
        want = (project or "").lower()
        hits: list = []
        seen: set = set()
        for k, s in enumerate(self._src):
            if want and s.project.lower() != want:
                continue
            parts = []
            if s.kind in kinds:
                names = s.name
                parts.append((_named_rows(s.table), lambda r, n=names: n[r].lower(), False))
            if "equipment" in kinds and s.kind == "variable":
                eq, it = s.equip, s.item
                parts.append((_item_rows(s.table), lambda r, e=eq, i=it: _item_key(e[r], i[r]), True))
            for order, keyf, item in parts:
                i = bisect.bisect_left(order, low, key=keyf)
                n = 0
                while i < len(order) and n < limit:
                    r = order[i]
                    kl = keyf(r)
                    if not kl.startswith(low):
                        break
                    hits.append((kl, 2 * k + item, self.code(k, r, item)))
                    i += 1
                    n += 1
        hits.sort()
        out = []
        for kl, _rank, c in hits:
            if kl not in seen:
                seen.add(kl)
                out.append(c)
                if len(out) >= limit:
                    return out
        more: list = []
        for k, s in enumerate(self._src):
            if want and s.project.lower() != want:
                continue
            if s.kind in kinds:
                text_, starts = _name_text(s.table)
                for r in _text_hits(text_, starts, low, _named_rows(s.table)):
                    more.append((s.name[r].lower(), 2 * k, self.code(k, r)))
                    if len(more) > 4 * limit:
                        break
            # (an Equip.Item text is built for a big table only when a filter asked for it;
            # the names that start with the text were found above)
            if (
                "equipment" in kinds
                and s.kind == "variable"
                and len(more) <= 4 * limit
                and (len(s.name) <= _ITEM_TEXT_ROWS or "item_text" in s.table._derived)
            ):
                text_, starts = _item_text(s.table)
                for r in _text_hits(text_, starts, low, _item_rows(s.table)):
                    more.append((_item_key(s.equip[r], s.item[r]), 2 * k + 1, self.code(k, r, True)))
                    if len(more) > 4 * limit:
                        break
        more.sort()
        for kl, _rank, c in more:
            if kl not in seen:
                seen.add(kl)
                out.append(c)
                if len(out) >= limit:
                    break
        return out

    def filter(
        self,
        codes,
        text: str,
        project: Optional[str] = None,
        columns: Sequence[str] = ("name", "type", "equip", "project", "comment"),
        kinds: Optional[Sequence[str]] = None,
        regex: bool = False,
    ) -> array:
        """Filter tag codes by text."""
        low = (text or "").strip().lower()
        rx = None
        if regex and low:
            try:
                rx = re.compile((text or "").strip(), re.IGNORECASE | re.MULTILINE)
            except re.error:
                return array("q")
        # what a cell is tested with, and what a joined column text is searched for
        if rx is not None:

            def test(v: str) -> bool:
                return rx.search(v) is not None

            q = rx
        else:

            def test(v: str) -> bool:
                return low in v.lower()

            q = low
        want = (project or "").lower()
        keep_src = [not want or s.project.lower() == want for s in self._src]
        if kinds is not None:
            kinds = set(kinds)
            names_ok = [keep_src[k] and s.kind in kinds for k, s in enumerate(self._src)]
            items_ok = [
                keep_src[k] and s.kind == "variable" and "equipment" in kinds for k, s in enumerate(self._src)
            ]
        else:
            names_ok = list(keep_src)
            items_ok = list(keep_src)
        if not low:
            if not want and kinds is None:
                return codes if isinstance(codes, array) else array("q", codes)
            out = array("q")
            for c in codes:
                if items_ok[(-c - 1) >> _SHIFT] if c < 0 else names_ok[c >> _SHIFT]:
                    out.append(c)
            return out
        if len(codes) <= max(_PER_CODE_MIN, self._row_count() // _PER_CODE_SHARE):
            return self._filter_codes(codes, test, columns, names_ok, items_ok)
        keep_src = [a or b for a, b in zip(names_ok, items_ok)]
        names_hit: list = []
        items_hit: list = []
        breathe, every = dbfcols.breathe, dbfcols.YIELD_ROWS
        for k, s in enumerate(self._src):
            breathe()
            n = len(s.name)
            if not keep_src[k]:
                names_hit.append(None)
                items_hit.append(None)
                continue
            whole = "project" in columns and test(s.project)
            mask = bytearray(b"\x01" * n) if whole else bytearray(n)
            if not whole:
                if "name" in columns:
                    text_, starts = _name_text(s.table)
                    for r in _text_hits(text_, starts, q, _named_rows(s.table)):
                        mask[r] = 1
                for col, colname, tcol in (
                    (s.type, "type", "TYPE"),
                    (s.equip, "equip", "EQUIP"),
                    (s.comment, "comment", "COMMENT"),
                ):
                    if colname not in columns or (tcol == "EQUIP" and s.kind == "local"):
                        continue
                    match = {v for v in _distinct(s.table, tcol) if v and test(v)}
                    if match:
                        for r, v in enumerate(col):
                            if not r % every:
                                breathe()
                            if v in match:
                                mask[r] = 1
            names_hit.append(mask if names_ok[k] else None)
            imask = None
            if s.kind == "variable" and items_ok[k]:
                imask = bytearray(mask)
                if not whole and "name" in columns:
                    text_, starts = _item_text(s.table)
                    for r in _text_hits(text_, starts, q, _item_rows(s.table)):
                        imask[r] = 1
            items_hit.append(imask)
        out = array("q")
        append = out.append
        for n, c in enumerate(codes):
            if not n % every:
                breathe()
            if c < 0:
                cc = -c - 1
                m = items_hit[cc >> _SHIFT]
                if m is not None and m[cc & _ROWMASK]:
                    append(c)
            else:
                m = names_hit[c >> _SHIFT]
                if m is not None and m[c & _ROWMASK]:
                    append(c)
        return out

    def prepare_search(self) -> None:
        """Build what filter and search read."""
        for s_ in self._src:
            t = s_.table
            dbfcols.breathe(True)
            builds = [lambda t=t: _name_text(t)]
            builds += [lambda t=t, c=c: _distinct(t, c) for c in ("TYPE", "EQUIP", "COMMENT")]
            # (every table's Equip.Item text too: else the first key over a large scope
            # builds the big tables' on the GUI thread)
            if s_.kind == "variable":
                builds.append(lambda t=t: _item_text(t))
            for build in builds:
                try:
                    build()
                except KeyError, ValueError:  # swallow-ok: a table without that column
                    pass

    def _row_count(self) -> int:
        return sum(len(src.name) for src in self._src)

    def _filter_codes(self, codes, test, columns, names_ok, items_ok) -> array:
        """Filter over a few codes."""
        out = array("q")
        append = out.append
        src = self._src
        use_name, use_type = "name" in columns, "type" in columns
        use_equip, use_comment = "equip" in columns, "comment" in columns
        use_project = "project" in columns
        breathe, every = dbfcols.breathe, dbfcols.YIELD_ROWS
        for n, c in enumerate(codes):
            if not n % every:
                breathe()
            item = c < 0
            cc = -c - 1 if item else c
            k, r = cc >> _SHIFT, cc & _ROWMASK
            if not (items_ok[k] if item else names_ok[k]):
                continue
            s_ = src[k]
            if use_project and test(s_.project):
                append(c)
                continue
            nm = s_.name[r]
            hit = (
                (use_name and bool(nm) and test(nm))
                or (use_type and bool(s_.type[r]) and test(s_.type[r]))
                or (use_equip and s_.kind != "local" and bool(s_.equip[r]) and test(s_.equip[r]))
                or (use_comment and bool(s_.comment[r]) and test(s_.comment[r]))
            )
            if not hit and item and use_name and s_.equip[r] and s_.item[r] and nm:
                hit = test(_item_key(s_.equip[r], s_.item[r]))
            if hit:
                append(c)
        return out

    def counts(self, codes) -> "OrderedDict[str, int]":
        """project -> how many of ``codes`` it defines, in scope order."""
        per = [0] * len(self._src)
        for c in codes:
            per[(-c - 1 if c < 0 else c) >> _SHIFT] += 1
        out: "OrderedDict[str, int]" = OrderedDict()
        for s, n in zip(self._src, per):
            if n:
                out[s.project] = out.get(s.project, 0) + n
        return out

    def sort_key(self, column: str) -> Callable[[int], object]:
        """A sort key over codes for a column ('name', 'kind', 'type', 'equip',
        'project', 'comment'), case-insensitive."""
        k = {"name": 0, "kind": 1, "type": 2, "equip": 3, "project": 4, "comment": 5}[column]
        cells = self.cells
        if k == 0:
            return lambda c: cells(c)[0].lower()
        # a tag code's column read straight from its table, lowered once per distinct value
        src = self._src
        col = {2: "TYPE", 3: "EQUIP", 5: "COMMENT"}.get(k)
        lows = []
        for s in src:
            if col is None:
                lows.append(None)
                continue
            try:
                lows.append({v: v.lower() for v in _distinct(s.table, col)})
            except KeyError, ValueError:  # swallow-ok: a table without that column
                lows.append(None)
        attr = {2: "type", 3: "equip", 5: "comment"}.get(k)

        def key(c: int):
            if c < 0:
                t = cells(c)
                return (t[k].lower(), t[0].lower())
            s, r = src[c >> _SHIFT], c & _ROWMASK
            if k == 1:
                v = s.kind
            elif k == 4:
                v = s.project.lower()
            else:
                raw = getattr(s, attr)[r]
                m = lows[c >> _SHIFT]
                v = m.get(raw) if m is not None else None
                if v is None:
                    v = raw.lower()
            return (v, s.name[r].lower())

        return key


_INDEXES: "OrderedDict[tuple, tuple]" = OrderedDict()
_INDEX_LOCK = threading.RLock()
#: tag indexes kept (the column tables they read are shared)
MAX_INDEXES = 8


def _scope_table_paths(sc: TS.TagScope, projects=None) -> list:
    """(project, variable.dbf, locvar.dbf, their stamps) of the scope's projects."""
    out = []
    for p in projects if projects is not None else sc.projects:
        vpath, vst = TS.stamp_in(p.dir, "variable.dbf")
        lpath, lst = TS.stamp_in(p.dir, "locvar.dbf")
        out.append((p, vpath, lpath, (vst, lst)))
    return out


class TagIndexStopped(Exception):
    """:func:`tag_index` was stopped by its progress callback (the Tags panel's Cancel)."""


def tag_index(
    project_dir: str, progress: Optional[Callable[[int, int, str], None]] = None, near_only: bool = False
) -> TagIndex:
    """Tags, local variables and Equip.Item references in the page folder's tag scope."""
    sc = TS.scope(project_dir)
    projects = sc.near() if near_only else list(sc.projects)
    paths = _scope_table_paths(sc, projects)
    sig = tuple(st for _p, _v, _l, st in paths)
    key = (sc.key, near_only)
    with _INDEX_LOCK:
        hit = _INDEXES.get(key)
        if hit is not None and hit[0] == sig:
            _INDEXES.move_to_end(key)
            return hit[1]
    sources = []
    for n, (p, vpath, lpath, _st) in enumerate(paths):
        dbfcols.breathe(True)
        if progress is not None:
            try:
                go = progress(n, len(paths), p.name)
            except Exception:  # noqa: BLE001 -- a progress report never stops the read
                go = None
            if go is False:
                # (the Tags panel's Cancel: nothing half read is kept as the scope's index)
                raise TagIndexStopped("the tag index of %s was stopped" % project_dir)
        for path, kind, cols in ((vpath, "variable", TAG_COLUMNS), (lpath, "local", _INDEX_LOCAL)):
            if not path:
                continue
            t = dbfcols.column_table(path, cols)
            if t is not None and len(t):
                sources.append(_Source(p.name, p.relation, kind, t))
    idx = TagIndex(sources=sources, scope=sc)
    with _INDEX_LOCK:
        hit = _INDEXES.get(key)
        if hit is not None and hit[0] == sig:
            # another thread (the Tags panel's reader, a warm-up) stored it
            # meanwhile: keep that one, so the lists built on it stay current
            _INDEXES.move_to_end(key)
            return hit[1]
        _INDEXES[key] = (sig, idx)
        _INDEXES.move_to_end(key)
        while len(_INDEXES) > MAX_INDEXES:
            _INDEXES.popitem(last=False)
    return idx


def cached_tag_index(project_dir: str) -> Optional[TagIndex]:
    """The page folder's whole-scope tag index when it is built and current, else None."""
    try:
        sc = TS.scope(project_dir)
    except Exception:  # noqa: BLE001
        return None
    sig = tuple(st for _p, _v, _l, st in _scope_table_paths(sc))
    with _INDEX_LOCK:
        hit = _INDEXES.get((sc.key, False))
    return hit[1] if hit is not None and hit[0] == sig else None


class TagFormats:
    """name -> display FORMAT over a scope's variable tables, first definition
    wins: '' for a tag without a FORMAT, None for a name no table defines. The
    tables are read on first use, in scope order (the page's own first)."""

    def __init__(self, paths: Sequence[str]):
        self._paths = list(paths)
        self._tables: list = [None] * len(self._paths)
        self._lock = threading.Lock()

    def _table(self, k: int):
        t = self._tables[k]
        if t is None:
            with self._lock:
                t = self._tables[k]
                if t is None:
                    t = dbfcols.column_table(self._paths[k], TAG_COLUMNS)
                    if t is None:
                        t = dbfcols.ColumnTable.from_columns(TAG_COLUMNS, [()] * len(TAG_COLUMNS))
                    self._tables[k] = t
        return t

    def get(self, low: str, default=None) -> Optional[str]:
        """The FORMAT of the tag named ``low`` (lower case, stripped)."""
        if "." in low:
            # a variable tag name has no '.': a dotted name is an Equip.Item reference or a tag
            # extension (Tag.Field), never a variable tag. Answering it here
            # keeps a page's display values from reading every table of the
            # scope (a large page asks for hundreds of Equip.Item references)
            return default
        for k in range(len(self._paths)):
            t = self._table(k)
            r = _find_row(t, low)
            if r is not None:
                return t.column("FORMAT")[r]
        return default

    def __contains__(self, low: str) -> bool:
        return self.get(low) is not None

    def __iter__(self):
        """The lower-case names, each once, in scope order (reads every table)."""
        seen = set()
        for k in range(len(self._paths)):
            for n in self._table(k).column("NAME"):
                low = n.lower()
                if low and low not in seen:
                    seen.add(low)
                    yield low

    def __call__(self, name: str) -> Optional[str]:
        return self.get((name or "").strip().lower())


_FORMATS: "OrderedDict[tuple, tuple]" = OrderedDict()


def tag_format_map(project_dir: str) -> Optional[TagFormats]:
    """Tag name -> FORMAT, for measuring number displays."""
    sc = TS.scope(project_dir)
    near = {id(p) for p in sc.near()}
    stamped = [x for x in sc.stamped_tables("variable.dbf") if id(x[0]) in near]
    paths = [path for _p, path, _st in stamped]
    if not paths:
        return None
    sig = tuple(st for _p, _path, st in stamped)
    with _INDEX_LOCK:
        hit = _FORMATS.get(sc.key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        fm = TagFormats(paths)
        _FORMATS[sc.key] = (sig, fm)
        while len(_FORMATS) > MAX_INDEXES:
            _FORMATS.popitem(last=False)
        return fm


# -- rows of the other scope tables --------------------------------------------------
class TableRow(Mapping):
    """One row of a column table as a read-only mapping (``PROJECT`` = the
    project it comes from): the equipment lists of a scope hold tens of
    thousands, a dict each would cost six times the memory."""

    __slots__ = ("_t", "_r", "_p")

    def __init__(self, table, row: int, project: str):
        self._t, self._r, self._p = table, row, project

    def __getitem__(self, key):
        if key == "PROJECT":
            return self._p
        if not self._t.has(key):
            raise KeyError(key)
        return self._t.column(key)[self._r]

    def get(self, key, default=None):
        if key == "PROJECT":
            return self._p
        if not self._t.has(key):
            return default
        return self._t.column(key)[self._r]

    def __iter__(self):
        return iter(self._t.columns + ("PROJECT",))

    def __len__(self) -> int:
        return len(self._t.columns) + 1

    def __repr__(self) -> str:
        return "TableRow(%r)" % dict(self)


_SCOPE_LISTS: "OrderedDict[tuple, tuple]" = OrderedDict()


def _scope_list(kind: str, project_dir: str, table: str, build: Callable) -> tuple:
    """``build(scope, [(project, path)])`` over the scope's ``table`` files,
    kept until one of them changes."""
    sc = TS.scope(project_dir)
    stamped = sc.stamped_tables(table)
    tables = [(p, path) for p, path, _st in stamped]
    sig = tuple(st for _p, _path, st in stamped)
    key = (kind, sc.key)
    with _INDEX_LOCK:
        hit = _SCOPE_LISTS.get(key)
        if hit is not None and hit[0] == sig:
            _SCOPE_LISTS.move_to_end(key)
            return hit[1]
    value = build(sc, tables)
    with _INDEX_LOCK:
        _SCOPE_LISTS[key] = (sig, value)
        _SCOPE_LISTS.move_to_end(key)
        while len(_SCOPE_LISTS) > 32:
            _SCOPE_LISTS.popitem(last=False)
    return value


def clear_scope_caches() -> None:
    """Forget every tag index, format map and scope list (tests; the caches
    re-validate by table stamps on their own)."""
    with _INDEX_LOCK:
        _INDEXES.clear()
        _FORMATS.clear()
        _SCOPE_LISTS.clear()
    TS.clear()


def trend_tags(project_dir: str) -> tuple[TrendTag, ...]:
    """The trend tags (trend.dbf) of the page folder's tag scope, first
    definition wins -- CtDraw's Insert Trend (help 1172143)."""

    def build(_sc, tables):
        out, seen = [], set()
        for p, path in tables:
            t = dbfcols.column_table(path, TREND_COLUMNS)
            if t is None:
                continue
            name, expr, com, typ, eu = (t.column(c) for c in TREND_COLUMNS)
            for r in range(len(t)):
                n = name[r]
                if n and n.lower() not in seen:
                    seen.add(n.lower())
                    out.append(TrendTag(n, p.name, expr[r], com[r], typ[r], eu[r]))
        return tuple(out)

    return _scope_list("trend", project_dir, "trend.dbf", build)


def equipment(project_dir: str) -> tuple:
    """The equip.dbf rows (NAME / TYPE / COMMENT / PAGE / CLUSTER / CONTENT and
    PROJECT, read-only mappings) of the page folder's tag scope, first
    definition of a name wins."""

    def build(_sc, tables):
        out, seen = [], set()
        for p, path in tables:
            t = dbfcols.column_table(path, EQUIP_COLUMNS)
            if t is None:
                continue
            names = t.column("NAME")
            for r in range(len(t)):
                n = names[r]
                if n and n.lower() not in seen:
                    seen.add(n.lower())
                    out.append(TableRow(t, r, p.name))
        return tuple(out)

    return _scope_list("equip", project_dir, "equip.dbf", build)


#: the menus of CtDraw's Access > General (CtDraw fills the combos
#: with the numbers 1..255 and 1..8)
AREAS = tuple(str(n) for n in range(1, 256))
PRIVILEGES = tuple(str(n) for n in range(1, 9))


def _chain_table(project_dir: str, table: str) -> tuple:
    """The rows (with PROJECT) of ``table`` in the page folder's tag scope,
    first definition of a NAME wins."""

    def build(_sc, tables):
        out, seen = [], set()
        for p, path in tables:
            for r in _cached_rows(path):
                name = r.get("NAME", "")
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    out.append(dict(r, PROJECT=p.name))
        return tuple(out)

    return _scope_list("rows:" + table, project_dir, table, build)


def devices(project_dir: str) -> tuple[dict, ...]:
    """The devices.dbf rows (NAME / TYPE / FORMAT / COMMENT ...) of the tag
    scope, first definition wins -- the Log device menu of Access > General
    (CtDraw lists the project's 'Devices' table)."""
    return _chain_table(project_dir, "devices.dbf")


def device_logs_messages(row: dict) -> bool:
    """A log device writes an object's command messages only when its format
    has the {MsgLog} field (help 1172178)."""
    return "{msglog" in (row.get("FORMAT") or "").lower()


def clusters(project_dir: str) -> tuple:
    """The cluster.dbf rows (NAME / COMMENT) of the tag scope -- the Cluster
    menu of Page Properties > General (CtDraw fills it from the project's
    'Cluster' table)."""
    return _chain_table(project_dir, "cluster.dbf")


def content_types(project_dir: str) -> tuple:
    """The contype.dbf rows (NAME / COMMENT: L1..L4, FP, General, Master,
    __Internal ...) of the tag scope -- the values of a page's CON_TYPE (Plant
    SCADA Studio's page grid, Situational Awareness pane autofill)."""
    return _chain_table(project_dir, "contype.dbf")


def keyboard_keys(project_dir: str) -> tuple:
    """The keys.dbf rows (NAME / CODE / COMMENT: ENTER, ESC, F2, LBUTTON_UP
    ...) of the tag scope -- the key menu of a key sequence (CtDraw lists the
    'Keyboard Keys' table)."""
    return _chain_table(project_dir, "keys.dbf")


# -- the menu configuration (pagemenu.dbf) ------------------------------------
# Plant SCADA Studio's Menu Configuration: one row per menu item, PAGE = the
# menu it belongs to ('Navigation', 'Headerbar', 'Help', 'InformationZone'
# ...), LEVEL1..LEVEL6 = its place in that menu, COMMAND / TARGETPAGE / ORDER
# / SYMBOL / COMMENT. The compiler merges the tables of the project and its
# included projects into one menu tree (the runtime's MenuGetPageNode(page)).
# Situational Awareness reads it by path: a navigation link's Menu Item is
# 'Level1.Level2.Level3' under the 'Navigation' menu (SA_Include
# NavigationLink.ci: MenuGetNodeByPath(MenuGetPageNode("Navigation"), path)),
# a tab bar's Default Tabs Menu Name is a menu (PAGE) name, and a tree view's
# Datasource is a menu name or the built-in '__EquipmentModel'.

#: the menu levels a pagemenu row can have
MENU_LEVELS = tuple("LEVEL%d" % n for n in range(1, 7))
#: the menu the SA navigation genies read ('Navigation', Navigation.ci
#: g_sNavigation_CoreNavigationMenu)
NAVIGATION_MENU = "Navigation"
#: the equipment hierarchy as a menu (Navigation.ci g_sNavigation_EquipmentModel)
EQUIPMENT_MODEL_MENU = "__EquipmentModel"


@dataclass(frozen=True)
class MenuItem:
    """One pagemenu.dbf row: the menu (``page``) it belongs to and its
    ``levels`` (LEVEL1.. up to the last non-empty one)."""

    page: str
    levels: Tuple[str, ...]
    command: str = ""
    target_page: str = ""
    comment: str = ""
    order: str = ""
    project: str = ""

    @property
    def path(self) -> str:
        """The item's menu path as SA writes it: 'MyPlant.F & B.Mixing (L3)'."""
        return ".".join(self.levels)


def menu_rows(project_dir: str) -> tuple:
    """Every pagemenu.dbf row (with PROJECT) of the tag scope, in table order
    -- a menu table has no unique NAME, so nothing is de-duplicated."""

    def build(_sc, tables):
        out = []
        for p, path in tables:
            for r in _cached_rows(path):
                out.append(dict(r, PROJECT=p.name))
        return tuple(out)

    return _scope_list("menus", project_dir, "pagemenu.dbf", build)


def menu_items(project_dir: str, page: Optional[str] = None) -> List[MenuItem]:
    """The menu items of the tag scope; ``page`` = only that menu's items
    (case-insensitive, as the runtime compares strings). Rows without a
    LEVEL1 are left out."""
    want = (page or "").strip().lower()
    out = []
    for r in menu_rows(project_dir):
        pg = (r.get("PAGE") or "").strip()
        if want and pg.lower() != want:
            continue
        levels = [(r.get(k) or "").strip() for k in MENU_LEVELS]
        while levels and not levels[-1]:
            levels.pop()
        if not levels or not levels[0]:
            continue
        out.append(
            MenuItem(
                pg,
                tuple(levels),
                (r.get("COMMAND") or "").strip(),
                (r.get("TARGETPAGE") or "").strip(),
                (r.get("COMMENT") or "").strip(),
                (r.get("ORDER") or "").strip(),
                r.get("PROJECT", ""),
            )
        )
    return out


def menu_pages(project_dir: str) -> List[str]:
    """The menus (distinct PAGE values) of the tag scope, sorted
    case-insensitively, each in its first spelling."""
    seen: dict = {}
    for r in menu_rows(project_dir):
        pg = (r.get("PAGE") or "").strip()
        if pg:
            seen.setdefault(pg.lower(), pg)
    return sorted(seen.values(), key=str.lower)


def menu_paths(project_dir: str, page: str = NAVIGATION_MENU) -> List[str]:
    """Every path of menu ``page`` an item can name -- each item's path and
    every level above it (a Level1 context, a Level2 tab) -- in table order,
    once each (case-insensitive)."""
    out, seen = [], set()
    for it in menu_items(project_dir, page):
        for n in range(1, len(it.levels) + 1):
            p = ".".join(it.levels[:n])
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
    return out


def equipment_reference_categories(project_dir: str) -> List[str]:
    """The categories (REFCAT: 'MEO', 'Interlock.Process' ...) of the
    equipment references (eqref.dbf) of the tag scope, in first-seen order --
    what a pane's Equipment Reference Associations name (help 1180289)."""

    def build(_sc, tables):
        seen: Dict[str, str] = {}
        for _p, path in tables:
            t = dbfcols.column_table(path, ("REFCAT",), unique=())
            for cat in t.column("REFCAT") if t is not None else ():
                if cat:
                    seen.setdefault(cat.lower(), cat)
        return tuple(seen.values())

    return list(_scope_list("eqref", project_dir, "eqref.dbf", build))


# -- the equipment hierarchy (equip.dbf NAME = 'Area.Line.Unit.Item') -----------


@dataclass(eq=False)
class EquipmentNode:
    """One level of the equipment hierarchy: ``name`` = this level's part,
    ``full`` = the dotted name, ``row`` = its equip.dbf row (None for a level
    no row defines, e.g. 'Company' above 'Company.TopMilk.Mixing')."""

    name: str
    full: str
    row: Optional[Mapping] = None
    children: List["EquipmentNode"] = field(default_factory=list)
    parent: Optional["EquipmentNode"] = field(default=None, repr=False)


def equipment_hierarchy(rows) -> List[EquipmentNode]:
    """The equipment ``rows`` (``equipment()``) as a tree split at '.', the
    levels in first-seen order; a level that no row defines is still a node
    (so every equipment has its parents)."""
    roots: List[EquipmentNode] = []
    index: Dict[str, EquipmentNode] = {}
    for r in rows:
        name = (r.get("NAME") or "").strip()
        if not name:
            continue
        parts = [p for p in name.split(".")]
        siblings = roots
        full = ""
        node = None
        for part in parts:
            full = part if not full else full + "." + part
            child = index.get(full.lower())
            if child is None:
                child = EquipmentNode(part, full, parent=node)
                index[full.lower()] = child
                siblings.append(child)
            node = child
            siblings = node.children
        if node is not None and node.row is None:
            node.row = r
    return roots


def chain_page_names(project_dir: str) -> list:
    """The registered page names (pages.DBF) of the tag scope, the project's
    own first -- what the Previous / Next page menus offer (a page of an
    included project, or of a project compiled with it, can be displayed too)."""

    def build(_sc, tables):
        out, seen = [], set()
        for _p, path in tables:
            t = dbfcols.column_table(path, ("NAME",))
            for name in t.column("NAME") if t is not None else ():
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    out.append(name)
        return tuple(out)

    return list(_scope_list("pages", project_dir, "pages.dbf", build))


def default_scan_time() -> str:
    """The [Page]ScanTime every page without its own scan time uses (CtDraw
    shows it in the disabled field while 'Default' is ticked; '250' when
    citect.ini does not set it)."""
    try:
        from .citect_config import ini_value

        v = (ini_value("page", "scantime", "250") or "250").strip()
    except Exception:  # noqa: BLE001 -- a hint only
        v = "250"
    return v if is_digits(v) else "250"


def page_area(page_path: str) -> str:
    """The AREA of a page's pages.DBF row ('' = none / no row): what an
    object 'in the same area as the page' belongs to."""
    try:
        name = os.path.splitext(os.path.basename(page_path))[0]
        rec = Project(os.path.dirname(os.path.abspath(page_path))).page_record(name)
    except Exception:  # noqa: BLE001 -- a hint only
        return ""
    return (rec or {}).get("AREA", "").strip()


# ------------------------------------------------------- genie form list values
# A genie-form field with a list form ('"Variable Tags", "NAME", "TYPE=DIGITAL"')
# is a drop-down of that project-database list (a help list or a database form).

#: help lists: the install's Bin\SCADAHELP.dbf / HELP.DBF (TYPE, DATA, FILTER,
#: LOCALIZE): the rows whose TYPE is the list field, DATA shown
_HELP_FORMS = ("help", "scadahelp")


def _bin_dirs() -> list[str]:
    from .cicode_catalog import _bin_dirs as dirs

    return dirs()


_FORM_TABLES: dict = {}


def form_tables() -> dict[str, str]:
    """lower list-form name -> its database table (the ``FORM "<name>", ...,
    "<table>", <INDEX|NONINDEX|WRITEINDEXED> ...`` lines of the install's
    Bin\\citect.frm: 'variable tags' -> 'variable', 'pages' -> 'pages',
    'contype' -> 'contype', 'labels' -> 'labels', 'equipment' -> 'equip')."""
    for d in _bin_dirs():
        p = _find_table(d, "citect.frm")
        if not p:
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        hit = _FORM_TABLES.get(p)
        if hit is not None and hit[0] == (st.st_mtime_ns, st.st_size):
            return hit[1]
        out: dict[str, str] = {}
        tok = re.compile(r'"([^"]*)"|([A-Za-z_]+)')
        try:
            text = open(p, "rb").read().decode("cp1252", "replace")
        except OSError:
            continue
        for line in text.splitlines():
            s = line.lstrip()
            if not s[:4].upper() == "FORM" or not s[4:5].isspace():
                continue
            strings = []
            for m in tok.finditer(s[4:]):
                if m.group(2) is not None:
                    break
                strings.append(m.group(1))
            if len(strings) >= 2:
                out.setdefault(strings[0].lower(), strings[-1])
        _FORM_TABLES[p] = ((st.st_mtime_ns, st.st_size), out)
        return out
    return {}


@dataclass(frozen=True)
class ListTerm:
    """One term of a list filter: ``field`` matches ``pattern`` as ``kind``
    ('any' no filter, 'exact', 'prefix' ``ABC*``, 'suffix' ``*ABC``, 'glob'
    other wildcards, 'regex' ``(...)``)."""

    field: str
    kind: str
    pattern: str = ""


def _unescape_glob(p: str) -> str:
    return re.sub(r"\^(.)", r"\1", p) if "^" in p else p


def parse_list_filter(list_field: str, list_filter: str = "") -> list[ListTerm]:
    """Parse a genie form field's list filter."""
    terms: dict[int, ListTerm] = {0: ListTerm(list_field, "any")}
    extra = 1
    for raw in (list_filter or "").split(","):
        if not raw.strip():
            continue
        fld, eq, pat = raw.partition("=")
        if not eq:
            fld, pat = list_field, raw
        fld = fld.strip()
        pat = pat.strip(" \t\n")
        if fld.lower() == list_field.lower():
            slot = 0
        else:
            slot = extra
            extra += 1
        if extra > 5:
            raise ValueError("a list filter can have at most five terms")
        terms[slot] = ListTerm(fld, _pattern_kind(pat), pat)
    return [terms[k] for k in sorted(terms)]


def _pattern_kind(pat: str) -> str:
    if not pat:
        return "any"
    if pat.startswith("("):
        return "regex"
    plain = pat.replace("^*", "").replace("^?", "")
    stars, qs = plain.count("*"), plain.count("?")
    if pat.startswith("*") and stars == 1 and qs == 0:
        return "suffix"
    if pat.endswith("*") and not pat.endswith("^*") and stars == 1 and qs == 0:
        return "prefix"
    if stars or qs:
        return "glob"
    return "exact"


def _glob_regex(pat: str) -> "re.Pattern":
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "^" and i + 1 < len(pat):
            out.append(re.escape(pat[i + 1]))
            i += 2
            continue
        out.append("(.+)" if c == "*" else "(.)" if c == "?" else re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE | re.DOTALL)


def list_filter_value(terms: list[ListTerm], row: dict) -> str | None:
    """What the drop-down shows for a table row (None = the row is filtered
    out): the list field's value, with a prefix / suffix term's fixed part
    removed and a wildcard term reduced to the wildcard parts."""

    def get(fld: str) -> str:
        v = row.get(fld)
        if v is None:
            low = fld.lower()
            v = next((val for k, val in row.items() if k.lower() == low), "")
        return (v or "").strip()

    shown = None
    for k, t in enumerate(terms):
        v = get(t.field)
        out = v
        if t.kind == "exact":
            if v.lower() != _unescape_glob(t.pattern).lower():
                return None
        elif t.kind == "prefix":
            p = _unescape_glob(t.pattern[:-1])
            if not v.lower().startswith(p.lower()):
                return None
            out = v[len(p) :]
        elif t.kind == "suffix":
            p = _unescape_glob(t.pattern[1:])
            if len(p) >= len(v) or not v.lower().endswith(p.lower()):
                return None
            out = v[: len(v) - len(p)]
        elif t.kind == "glob":
            m = _glob_regex(t.pattern).match(v)
            if m is None:
                return None
            out = "".join(m.groups())
        elif t.kind == "regex":
            try:
                if re.search(t.pattern, v, re.IGNORECASE) is None:
                    return None
            except re.error:
                return None
        if k == 0:
            shown = out
    return shown


_LIST_CACHE: dict = {}


def list_values(project_dir: str, list_form: str, list_field: str, list_filter: str = "") -> list[str] | None:
    """The drop-down values of a genie form field."""
    if not list_form or not list_field:
        return None
    form = list_form.strip()
    low = form.lower()
    if low in _HELP_FORMS:
        if list_field.strip().lower() == "function":
            from .cicode_catalog import FunctionCatalog

            return [fn.name for fn in FunctionCatalog(project_dir).all()] or None
        return _help_values(list_field, list_filter)
    table = form_tables().get(low) or form
    tables = TS.scope(project_dir).tables(table.lower() + ".dbf")
    if not tables:
        return None
    sig = tuple(TS.table_stamp(p) for _pr, p in tables)
    key = (sig, list_field.lower(), list_filter)
    hit = _LIST_CACHE.get(key)
    if hit is not None:
        return list(hit)
    fields = [f.strip() for f in list_field.split(".", 1)]
    try:
        terms = parse_list_filter(fields[0], list_filter)
    except ValueError:
        return None
    columns = []
    for c in [fields[0]] + fields[1:] + [t.field for t in terms]:
        if c and c.upper() not in (x.upper() for x in columns):
            columns.append(c)
    out, seen = [], set()
    for pr, p in tables:
        t = dbfcols.column_table(p, columns, unique=(fields[0],))
        if t is None:
            continue
        second_col = t.column(fields[1]) if len(fields) > 1 else None
        for r in range(len(t)):
            v = list_filter_value(terms, TableRow(t, r, pr.name))
            if v is None or not v:
                continue
            if second_col is not None:
                second = (second_col[r] or "").strip()
                if second:
                    v = v + "." + second
            if v.lower() not in seen:
                seen.add(v.lower())
                out.append(v)
    out.sort(key=str.lower)
    if len(_LIST_CACHE) > 64:
        _LIST_CACHE.clear()
    _LIST_CACHE[key] = tuple(out)
    return out


def _help_values(list_field: str, list_filter: str) -> list[str] | None:
    """the help rows whose TYPE is list_field (a row with a
    FILTER shows only for that list filter), in their stored order. Rows
    marked LOCALIZE use strings from the installation's SCADA help resources."""
    if list_field.strip().upper() == "FONTTYPE":
        return None
    want = list_field.strip().lower()
    flt = (list_filter or "").strip().lower()
    for d in _bin_dirs():
        out = []
        for name in ("scadahelp.dbf", "help.dbf"):
            p = _find_table(d, name)
            for r in _cached_rows(p) if p else []:
                if r.get("TYPE", "").strip().lower() != want:
                    continue
                f = r.get("FILTER", "").strip().lower()
                if f and f != flt:
                    continue
                v = r.get("DATA", "").strip()
                if r.get("LOCALIZE", "").strip() == "1":
                    from .help_strings import translate

                    v = translate(v, d)
                if v and v not in out:
                    out.append(v)
        if out:
            return out
    return None


if __name__ == "__main__":
    import sys

    p = Project(sys.argv[1] if len(sys.argv) > 1 else default_project())
    tags = p.variable_tags()
    print("project:", p.name, "| variable tags:", len(tags))
    for t in tags[:8]:
        print("  %-22s %-8s %-12s %s" % (t.name, t.type, t.unit, t.comment))


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("project.widths", _WIDTHS_CACHE, on=("refresh", "tests"))
_caches.register("project.lists", _LIST_CACHE, on=("refresh", "tests"))


# the tag indexes, format maps and scope lists (View > Refresh re-reads the project tables)
# and the genie form tables
def _clear_index_tables() -> None:
    with _INDEX_LOCK:
        _INDEXES.clear()
        _FORMATS.clear()
        _SCOPE_LISTS.clear()


_caches.register("project.tag_indexes", _INDEXES, clear=_clear_index_tables, on=("refresh", "tests"))
_caches.register("project.tag_formats", _FORMATS, clear=_clear_index_tables, on=("refresh", "tests"))
_caches.register("project.scope_lists", _SCOPE_LISTS, clear=_clear_index_tables, on=("refresh", "tests"))
_caches.register("project.form_tables", _FORM_TABLES, on=("refresh", "tests"))
