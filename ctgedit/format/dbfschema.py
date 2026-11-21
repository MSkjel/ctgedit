"""The project table layouts CtDraw and Studio expect."""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..infra import caches as _caches

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dbf_schema.json")
#: the schema entries no form names (their keys in the JSON)
MASTER = "master"
LANGUAGE = "language"
#: (NAME, type, size, decimals)
Field = Tuple[str, str, int, int]

_INDEX_MODES = ("NONINDEX", "INDEX", "WRITEINDEXED", "UNIQUEINDEX")


@dataclass(frozen=True)
class Form:
    """One FORM block of citect.frm."""

    menu: str  # the menu name (what genie forms and lists name)
    title: str  # the form title ('' when the line has none)
    table: str  # the table's file name, as the line spells it
    index: str  # NONINDEX / INDEX / WRITEINDEXED
    lines: Tuple[Tuple[int, str, int, str], ...]  # (number, NAME, size, mode)
    index_key: str = ""  # the KEY field of an indexed form ('' for none)

    @property
    def indexed(self) -> bool:
        """The form keeps an index (INDEX, WRITEINDEXED, UNIQUEINDEX with a KEY)."""
        return self.index not in ("", "NONINDEX") and bool(self.index_key)

    @property
    def key(self) -> str:
        return self.table.lower()


_FORM = re.compile(r"\s*FORM\s+(.*?);?\s*$", re.I)
_TOKEN = re.compile(r'"([^"]*)"|([A-Za-z_]+)')
_KEY = re.compile(r'\bKEY\s+"([^"]+)"', re.I)
_FIELD = re.compile(r'\s*(\d+)\s*:\s*"([^"]+)"\s*,\s*(\d+)\s*(?:,\s*([A-Za-z_0-9]+))?')
#: a line that ends a form's field list
_SECTION = re.compile(
    r"\s*(NORMAL|EXTENDED|PRINT|MENU|COMMAND|FORM|PRINTHDR|"
    r"DEFAULTFILES|PARAM)\b",
    re.I,
)


def parse_frm(text: str) -> List[Form]:
    """The FORM blocks of a citect.frm text, in file order. Comment lines start with '!';
    a form's field lines run from its FORM line to its NORMAL / EXTENDED / PRINT text."""
    forms: List[Form] = []
    head = None
    key = ""
    lines: List[Tuple[int, str, int, str]] = []

    def close():
        if head is not None:
            forms.append(Form(*head, tuple(lines), key))

    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("!"):
            continue
        m = _FORM.match(s)
        if m and s[:4].upper() == "FORM" and s[4:5].isspace():
            close()
            strings, index, key = [], "", ""
            for t in _TOKEN.finditer(m.group(1)):
                if t.group(2) is not None:
                    if t.group(2).upper() in _INDEX_MODES:
                        index = t.group(2).upper()
                        k = _KEY.search(m.group(1), t.end())
                        key = k.group(1) if k else ""
                    break
                strings.append(t.group(1))
            lines = []
            if len(strings) >= 2:
                head = (strings[0], strings[1] if len(strings) > 2 else "", strings[-1], index)
            else:
                head = None
            continue
        if head is None:
            continue
        if _SECTION.match(s):
            close()
            head, lines = None, []
            continue
        f = _FIELD.match(s)
        if f:
            lines.append((int(f.group(1)), f.group(2), int(f.group(3)), f.group(4) or ""))
    close()
    return forms


def forms_by_table(forms: Sequence[Form]) -> Dict[str, Form]:
    """lower table name -> its form (the first, should two name one table)."""
    out: Dict[str, Form] = {}
    for f in forms:
        out.setdefault(f.key, f)
    return out


def read_frm(path: str) -> List[Form]:
    """:func:`parse_frm` of a citect.frm file (the install's ANSI text)."""
    with open(path, "rb") as fh:
        return parse_frm(fh.read().decode("cp1252", "replace"))


def form_fields(form: Form) -> List[Field]:
    """A form's fields in its order: every one a character field without decimals, its
    name as the form spells it; a name listed again (Users: PASSWORD as 3 and 7) is the
    first one only (the tables hold it once). A gap in the numbering is no field."""
    out: List[Field] = []
    seen = set()
    for _n, name, size, _mode in form.lines:
        if name.upper() in seen:
            continue
        seen.add(name.upper())
        out.append((name, "C", size, 0))
    return out


# ----------------------------------------------------------------------------- the schema
_SCHEMA_CACHE: Dict[str, dict] = {}


def load(path: str = SCHEMA_PATH) -> dict:
    """The committed schema (``{'tables': {name: {'fields': [...], ...}}, ...}``)."""
    hit = _SCHEMA_CACHE.get(path)
    if hit is None:
        with open(path, encoding="utf-8") as fh:
            hit = json.load(fh)
        _SCHEMA_CACHE[path] = hit
    return hit


_caches.register("dbfschema.schema", _SCHEMA_CACHE, on=("tests",))


def tables(path: str = SCHEMA_PATH) -> List[str]:
    return sorted(load(path)["tables"])


def fields(table: str, path: str = SCHEMA_PATH) -> List[Field]:
    """The fields of ``table`` (a form's table name, ``master`` or ``language``; any case)
    in the order a new table has them. KeyError when the schema has no such table."""
    ent = load(path)["tables"][table.lower()]
    return [(str(n), str(t), int(w), int(d)) for n, t, w, d in ent["fields"]]


def index_field(table: str, path: str = SCHEMA_PATH) -> str:
    """The field ``table``'s ``<table>.NDX`` is keyed on ('' when the table keeps no
    index: a NONINDEX form, MASTER.DBF). KeyError when the schema has no such table."""
    return str(load(path)["tables"][table.lower()].get("index", ""))


def form_index(form: Form) -> str:
    """The field a form's table is indexed on ('' for none): its KEY, as the field
    list spells it."""
    if not form.indexed:
        return ""
    for n, *_r in form_fields(form):
        if n.upper() == form.index_key.upper():
            return n
    return ""


# ----------------------------------------------------------------------------- new tables
class FieldTooLong(ValueError):
    """A value that does not fit its field (a new table never cuts one)."""


def table_bytes(
    table: str, rows: Sequence = (), when=None, layout: Optional[Sequence[Field]] = None
) -> bytes:
    """A new ``table`` in the schema's layout."""
    from . import dbf
    from .textcodec import encode_field

    lay = list(layout) if layout is not None else fields(table)
    widths = {n.upper(): w for n, _t, w, _d in lay}
    order = [n.upper() for n, _t, _w, _d in lay]
    for k, row in enumerate(rows):
        if not isinstance(row, dict) and len(row) > len(order):
            raise ValueError(
                "%s row %d has %d values, the table has %d fields" % (table, k + 1, len(row), len(order))
            )
        items = row.items() if isinstance(row, dict) else zip(order, row)
        for name, v in items:
            w = widths.get(str(name).upper())
            if w is None:
                raise ValueError("%s has no field %s (row %d)" % (table, name, k + 1))
            raw = encode_field("" if v is None else v, "the %s field of %s" % (name, table))
            if len(raw) > w:
                raise FieldTooLong(
                    "%s row %d: the %s field holds %d characters (%r), its "
                    "size is %d" % (table, k + 1, str(name).upper(), len(raw), str(v), w)
                )
    return dbf.table_bytes([(n, w) for n, _t, w, _d in lay], rows, when)


def create_table(
    path: str,
    table: str,
    rows: Sequence = (),
    when=None,
    indexes: Optional[Sequence[str]] = None,
    layout: Optional[Sequence[Field]] = None,
) -> None:
    """Write a new table (:func:`table_bytes`) at ``path`` and its index ``<table>.NDX``
    beside it (dbf.create_table): on the field named in ``indexes``, or by default on the
    schema's :func:`index_field` (none for a table the schema has not; ``()`` for none)."""
    from . import dbf

    lay = list(layout) if layout is not None else fields(table)
    if indexes is None:
        try:
            key = index_field(table)
        except KeyError:
            key = ""
        indexes = [key] if key else []
    table_bytes(table, rows, when, lay)  # (the checks)
    dbf.create_table(path, [(n, w) for n, _t, w, _d in lay], rows, when, indexes)


# ----------------------------------------------------------------------------- checks
def read_fields(path: str) -> List[Field]:
    """The field descriptors of a .DBF file: [(NAME, type, size, decimals)]."""
    with open(path, "rb") as fh:
        head = fh.read(32)
        if len(head) < 32:
            raise ValueError("%s: not a DBF" % path)
        hlen = struct.unpack_from("<H", head, 8)[0]
        desc = fh.read(max(0, hlen - 32))
    out: List[Field] = []
    for o in range(0, len(desc) - 31, 32):
        if desc[o] == 0x0D:
            break
        name = desc[o : o + 11].split(b"\0")[0].decode("cp1252", "replace")
        out.append((name, chr(desc[o + 11]), desc[o + 16], desc[o + 17]))
    return out


def ndx_key(path: str) -> Optional[Tuple[str, int]]:
    """(key expression, key length) of an ``.NDX`` file, or None when there is none or it
    cannot be read."""
    try:
        with open(path, "rb") as fh:
            h = fh.read(512)
    except OSError:
        return None
    if len(h) < 244:
        return None
    return (h[24:244].split(b"\0")[0].decode("cp1252", "replace").strip(), struct.unpack_from("<H", h, 12)[0])


def index_problems(table_path: str, field: str, width: int, label: str = "") -> List[str]:
    """How the index beside ``table_path`` (``<table>.NDX``, any case) differs from one on
    ``field`` of ``width``, one line ([] when it is right): no index, or one on another
    field or key length."""
    folder = os.path.dirname(os.path.abspath(table_path))
    stem = os.path.splitext(os.path.basename(table_path))[0].lower()
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    ndx = next((os.path.join(folder, f) for f in sorted(names) if f.lower() == stem + ".ndx"), "")
    label = (label + ": ") if label else ""
    nk = ndx_key(ndx) if ndx else None
    if nk is None:
        return ["%sthe index on %s (%s.NDX) is missing" % (label, field, stem)]
    if nk != (field.upper(), width):
        return [
            "%sthe index is on %s (key %d), not %s (%d)" % (label, nk[0] or "no field", nk[1], field, width)
        ]
    return []


def compare(
    have: Sequence[Field], want: Sequence[Field], table: str = "", order: bool = False, extra: bool = True
) -> List[str]:
    """How ``have`` differs from ``want``, one line each: a field of another size, type
    or decimals, a missing field, a field ``want`` has not (``extra``), and with ``order``
    a field out of place or a name spelled in another case. Names match in any case."""
    label = (table + ": ") if table else ""
    hv = {n.upper(): (n, t, w, d) for n, t, w, d in have}
    wv = {n.upper(): (n, t, w, d) for n, t, w, d in want}
    out = []
    for key, (n, t, w, d) in wv.items():
        got = hv.get(key)
        if got is None:
            out.append("%sthe field %s (size %d) is missing" % (label, n, w))
            continue
        if got[2] != w:
            out.append("%sthe field %s has size %d, not %d" % (label, n, got[2], w))
        if got[1] != t or got[3] != d:
            out.append(
                "%sthe field %s is type %s with %d decimals, not %s with %d"
                % (label, n, got[1], got[3], t, d)
            )
        if order and got[0] != n:
            out.append("%sthe field %s is spelled %s" % (label, n, got[0]))
    if extra:
        for key, (n, _t, w, _d) in hv.items():
            if key not in wv:
                out.append("%sthe field %s (size %d) is not in the layout" % (label, n, w))
    if order and not out:
        hn = [n.upper() for n, *_r in have]
        wn = [n.upper() for n, *_r in want]
        if hn != wn:
            out.append("%sthe fields are in another order: %s" % (label, ", ".join(hn)))
    return out
