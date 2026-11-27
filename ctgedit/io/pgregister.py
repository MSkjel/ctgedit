"""Write the project database rows a page save needs, as CtDraw does."""

from __future__ import annotations

import datetime as _dt
import os
import re
import struct
import time
import uuid
from typing import Dict, List, Optional, Tuple

from ..format.textcodec import CODEC
from ..format import codes as _codes

# (module level: the register and the page model read every record through these)
from ..format import colourstate as _CS
from ..format import geometry as _G
from ..format import placement as _P
from ..format.page import ASCII_FAST
from ..infra.paths import kill_point

_U16 = struct.Struct("<H").unpack_from


class PageRecordError(ValueError):
    """A staged Page Properties edit a save could not write (the page has no
    pages.DBF row, the project has no pgkey.DBF): the save's registration
    is rolled back and the edits stay staged."""


class ProjectDatabaseInUse(OSError):
    """A table of the project database cannot be written now: another program (Plant SCADA
    Studio) holds it open. Nothing was written."""

    def __init__(self, path: str, why: str = ""):
        super().__init__(
            "%s of project %s is in use by another program (is Plant SCADA "
            "Studio open?)%s"
            % (
                os.path.basename(path),
                os.path.basename(os.path.dirname(os.path.abspath(path))),
                ": %s" % why if why else "",
            )
        )
        self.path = path


# library class type code -> its table
LIB_TABLE = {
    0x1001: "pgbutton",
    0x1002: "pgbar",
    0x1003: "pgtrend",
    0x1004: "pgnum",
    0x1005: "pgstr",
    0x1006: "pgtext",
    0x1007: "pgsym",
    0x1008: "pgcol",
    0x1009: "pgadv",
    _codes.TC.KEYBOARD: "pgkey",
    0x100D: "pgansym",
    0x100E: "pgsizmve",
    0x100F: "pgslider",
    _codes.TC.TOUCH: "pgtouch",
}
DUMMY_TC = 0xFFFF

# the fields of every page table (Bin\DIR.DBF / the project DBF headers)
TABLE_FIELDS: Dict[str, List[str]] = {
    "pgdynobj": "PAGE TYPE AN GROUP SZX SZXMINPC SZXMAXPC SZXORG SZXMODE SZY SZYMINPC SZYMAXPC "
    "SZYORG SZYMODE ROT ROTSL ROTMINPC ROTMAXPC ROTXORG ROTYORG ROTMODE ROTSLMODE MVX "
    "MVXSL MVXMINPT MVXMAXPT MVXMODE MVXSLMODE MVY MVYSL MVYMINPT MVYMAXPT MVYMODE "
    "MVYSLMODE VISIBLE VISMODE COL_A COL_B COL_C COL_D COL_E COL_ARR COL_EXP COLMODE "
    "TXT_A TXT_B TXT_C TXT_D TXT_E TXT_STR SET_ARR TXTMODE FIL FILMINPC FILMAXPC "
    "FILMODE DISABLED DISMODE GREY_STYLE DESC COMMENT AREA PRIV MSG_LOG DEVICE".split(),
    "pgadv": "PAGE AN CMD COMMENT SYMBOL".split(),
    "pgalmind": "PAGE AN EXPR INCLUDE INSIDE WIDTH PADDING COLOUR FLAG_ON FLAG_LOC EQHIER".split(),
    "pgansym": "PAGE AN EXPR OFF_SYMBOL SYMBOL_1 SYMBOL_2 SYMBOL_3 SYMBOL_4 SYMBOL_5 SYMBOL_6 "
    "SYMBOL_7 SYMBOL_8 MODE COMMENT VISIBLE".split(),
    "pgbar": "PAGE AN EXPR BAR COMMENT VISIBLE".split(),
    "pgbutton": "PAGE AN TEXT FONT WIDTH HEIGHT CMD PRIV MSG_LOG DEVICE UP_AN DOWN_AN LEFT_AN "
    "RIGHT_AN COMMENT DOWN_CMD REPEAT_CMD DISMODE DISABLED GREY_STYLE VIS_MODE "
    "VISIBLE AREA RPT_RATE MVX MVXMINPT MVXMAXPT MVXMODE MVY MVYMINPT MVYMAXPT "
    "MVYMODE".split(),
    "pgcol": "PAGE AN VAR_A VAR_B VAR_C COLR000 COLR001 COLR010 COLR011 COLR100 COLR101 COLR110 "
    "COLR111 COMMENT VISIBLE".split(),
    "pgkey": "PAGE AN KEY_SEQ CMD PRIV MSG_LOG DEVICE UP_AN DOWN_AN LEFT_AN RIGHT_AN COMMENT AREA".split(),
    "pgnum": "PAGE AN EXPR FONT COMMENT VISIBLE".split(),
    "pgsizmve": "PAGE AN SIZEX SIZEY POSITIONX POSITIONY COMMENT HOTOFFSETX HOTOFFSETY".split(),
    "pgslider": "PAGE AN TAG THUMB X_SPAN Y_SPAN PRIV MODE COMMENT VISIBLE".split(),
    "pgstr": "PAGE AN EXPR OFF_TEXT OFF_FONT ON_TEXT ON_FONT COMMENT VISIBLE".split(),
    "pgsym": "PAGE AN VAR_A VAR_B VAR_C SYMB000 SYMB001 SYMB010 SYMB011 SYMB100 SYMB101 SYMB110 "
    "SYMB111 MODE COMMENT VISIBLE".split(),
    "pgtext": "PAGE AN VAR_A VAR_B VAR_C STR000 STR001 STR010 STR011 STR100 STR101 STR110 STR111 "
    "FONT000 FONT001 FONT010 FONT011 FONT100 FONT101 FONT110 FONT111 COMMENT "
    "VISIBLE".split(),
    "pgtouch": "PAGE AN ACTION RPT_RATE CMD DOWN_CMD DBLCLK_CMD REPEAT_CMD MSG_LOG MSG_LOGDN "
    "MSG_LOGDBL MSG_LOGRPT".split(),
    "pgtrend": "PAGE AN TRENDNO PEN1 PEN2 PEN3 PEN4 PEN5 PEN6 PEN7 PEN8 CMD COMMENT VISIBLE PRIV "
    "AREA DEVICE DESC PERIODIC CLUSTER".split(),
    "pgvertex": "PAGE INDEX AN EXPR X1 Y1 X2 Y2".split(),
}
PAGE_TABLES = tuple(TABLE_FIELDS)
ACTIVEX_TC = 0x0D
ACTIVEX_FIELDS = ["PAGE", "AN", "OBJ_NAME", "OBJ_GUID"]
LANG_MAX = 100  # PGLANG.NATIVE width; longer strings are refused
_CFB_SIG = bytes.fromhex("d0cf11e0a1b11ae1")
_FIXED = {"PAGE", "AN", "TYPE", "GROUP", "INDEX"}
#: = editing.USER_MARKED_KEYS: the Access fields of a genie member
_USER_MARKED = {"DESC", "COMMENT", "AREA", "PRIV", "DEVICE"}
_ALMIND = ("EXPR", "INCLUDE", "INSIDE", "WIDTH", "PADDING", "COLOUR", "FLAG_ON", "FLAG_LOC", "EQHIER")
_VERT_RE = re.compile(r"^VERTEX_VERTEXP(\d+)$")


# --------------------------------------------------------------------------- records
def _records(parsed):
    """[(index, PageObject, parent index)] in record (DFS) order."""
    objs = parsed.page.objects if hasattr(parsed, "page") else parsed
    par = [None] * len(objs)
    stack: list = []
    for i, o in enumerate(objs):
        while stack and stack[-1][1] <= 0:
            stack.pop()
        if stack:
            par[i] = stack[-1][0]
            stack[-1][1] -= 1
        if o.class_flag:
            stack.append([i, o.class_flag])
    return objs, par


def _an(po) -> Optional[int]:
    h = _G.get_handle(po)
    return None if h is None else (h + 1) & 0xFFFF


#: page name (lower case) -> {(dwFlags, body): value bag pairs} of its last
#: page_rows: a save registers every record of the page, and the records an
#: edit left alone are the same bytes as at the previous save. Only the last
#: call's records are kept (their bodies belong to the open page anyway).
_VALUE_BAGS: Dict[str, Dict[tuple, tuple]] = {}
_VALUE_BAG_PAGES = 8


def value_bag_memo(page_name: Optional[str]) -> tuple:
    """The memo :func:`_value_bag` reads for ``page_name``: the value bags the page's last
    page_rows kept, with a dict of this caller's own (a reader of the same records, the
    language strings or the page model, reads them, not walks them again)."""
    return (_VALUE_BAGS.get((page_name or "").lower()) or {}, {})


def _value_bag(po, memo: Optional[tuple] = None) -> List[tuple]:
    """The record's level-0 SUBSTITUTED value bag (e0.b1) as (key, value) pairs:
    genie_place.decode(po).entries[0][1] (empty when the record does not
    decode), read without decoding the rest of the record. ``memo`` = (the
    page's previous bags, this call's) -- page_rows."""
    if not (po.subtype_size & 0x8):
        return []
    body = po.body_raw
    key = (po.subtype_size, body) if memo is not None and type(body) is bytes else None
    got = None
    if key is not None:
        got = memo[1].get(key)
        if got is None:
            got = memo[0].get(key)
    if got is None:
        got = _value_bag_walk(po)
    if key is not None:
        memo[1][key] = got
    return list(got)


def _value_bag_walk(po) -> tuple:
    """The value bag e0.b1, without decoding every section."""
    b = po.body_raw
    try:
        if _CS.layout(po) is None:
            return ()
        _P.read_slots_of(po)
        o = _G.parse_geom_header(b).end
        if po.subtype_size & 0x1:
            o += 2 + struct.unpack_from("<H", b, o)[0]
        o += 2  # u16 N: N + 1 entries follow
        _size, cnt = struct.unpack_from("<HH", b, o)
        o += 4
        for _ in range(cnt):  # entry 0, bag 0 (declarations)
            o += 2 + struct.unpack_from("<H", b, o)[0]
            o += 2 + struct.unpack_from("<H", b, o)[0]
        _size, cnt = struct.unpack_from("<HH", b, o)
        o += 4
        pairs = []
        u16, fast = _U16, ASCII_FAST
        for _ in range(cnt):  # entry 0, bag 1 (the values)
            (n,) = u16(b, o)
            k = b[o + 2 : o + 2 + n]
            o += 2 + n
            (n,) = u16(b, o)
            v = b[o + 2 : o + 2 + n]
            o += 2 + n
            # (an ASCII key / value through the C codec: the same string, page.ASCII_FAST)
            pairs.append(
                (
                    k.decode("latin-1") if fast and k.isascii() else k.decode(CODEC),
                    v.decode("latin-1") if fast and v.isascii() else v.decode(CODEC),
                )
            )
        return tuple(pairs)
    except Exception:  # noqa: BLE001 -- decode fails alike
        return ()


def _txtmode_after_colmode(bag) -> bool:
    """TXTMODE='0' that comes AFTER COLMODE in the value bag never reaches the
    row."""
    seen = False
    for k, _v in bag:
        K = k.upper()
        if K == "COLMODE":
            seen = True
        elif K == "TXTMODE":
            return seen
    return False


def _off_before_mode(keys) -> bool:
    """TXT_OFF comes before TXTMODE in ``keys`` (upper case)."""
    if "TXTMODE" not in keys:
        return False
    return "TXT_OFF" in keys[: keys.index("TXTMODE")]


def _key_between(keys) -> bool:
    """A key of the record's own (not '!*' sheet state) lies between TXT_OFF and TXTMODE."""
    a, b = keys.index("TXT_OFF"), keys.index("TXTMODE")
    return any(not k.startswith("!") for k in keys[a + 1 : b])


def _txtmode_dropped(po, bag) -> bool:
    """TXTMODE '0' is left out of the text's pgdynobj row. CtDraw's number setter (FUN_0041eef0)
    deletes a key set to 0 and the save writes it back after later keys, so the key order
    tells: after COLMODE; a one-level genie member with TXT_OFF first in template and bag and a
    key of its own between; a free text with TXT_OFF first and a TXT_A."""
    if _txtmode_after_colmode(bag):
        return True
    keys = [k.upper() for k, _v in bag]
    if po.type_code != 0x05 or not _off_before_mode(keys):
        return False
    try:
        from ..format.genie_place import decode

        e = decode(po).entries
    except Exception:  # noqa: BLE001  # swallow-ok: a record that does not decode keeps the field
        return False
    if len(e) == 1:
        return any(k.upper() == "TXT_A" and v for k, v in bag)
    if len(e) != 2 or len(e[0]) < 3:
        return False
    return _off_before_mode([k.upper() for k, _v in e[0][2].pairs]) and _key_between(keys)


_WANT: Dict[tuple, Dict[str, str]] = {}


def _fields_from(bag, fields) -> Dict[str, str]:
    key = tuple(fields)
    want = _WANT.get(key)
    if want is None:
        want = _WANT[key] = {f.upper(): f for f in fields if f not in _FIXED}
    out: Dict[str, str] = {}
    for k, v in bag:
        f = want.get(k.upper())
        if f is not None and f not in out:
            if f in _USER_MARKED and v.endswith("<User>"):
                # a genie member's '<User>' marker is not stored
                v = v[: -len("<User>")]
            out[f] = v
    return out


def _parse(ctg):
    from ..format.page import parse_ctg, parse_ctg_bytes

    if isinstance(ctg, (bytes, bytearray)):
        return parse_ctg_bytes(bytes(ctg))
    if isinstance(ctg, (str, os.PathLike)):
        return parse_ctg(os.fspath(ctg))
    return ctg


def page_rows(ctg, page_name: str, resolver=None) -> Dict[str, List[Dict[str, str]]]:
    """The rows CtDraw's register writes for a page, per pg* table, in write
    order. ``ctg`` = a ``.ctg`` path, its bytes, or a parsed page. ``resolver``
    is accepted for API symmetry; the rows need no library (a page record
    carries its substituted values)."""
    objs, par = _records(_parse(ctg))
    out: Dict[str, List[Dict[str, str]]] = {t: [] for t in PAGE_TABLES}
    pkey = (page_name or "").lower()
    memo = (_VALUE_BAGS.pop(pkey, {}), {})
    for i, po in enumerate(objs):
        dw, tc = po.subtype_size, po.type_code
        if not (dw & 0x10):
            continue
        an = _an(po)
        if an is None:
            continue
        bag = _value_bag(po, memo)
        pa = par[i]
        if tc < 0x1000 or tc == DUMMY_TC:
            row = {
                "PAGE": page_name,
                "TYPE": "0x%04X" % tc,
                "AN": str(an),
                "GROUP": str(_an(objs[pa])) if pa is not None else "",
            }
            row.update(_fields_from(bag, TABLE_FIELDS["pgdynobj"]))
            if row.get("TXTMODE") == "0" and _txtmode_dropped(po, bag):
                del row["TXTMODE"]
            out["pgdynobj"].append(row)
        else:
            t = LIB_TABLE.get(tc)
            if t is not None:
                row = {"PAGE": page_name, "AN": str(an)}
                row.update(_fields_from(bag, TABLE_FIELDS[t]))
                out[t].append(row)
        d = {k.upper(): v for k, v in bag}
        if d.get("AI_ON", "").strip() == "1":
            row = {"PAGE": page_name, "AN": str(an)}
            for f in _ALMIND:
                v = d.get("AI_" + f)
                if v is not None:
                    row[f] = v
            out["pgalmind"].append(row)
        # one row per vertex with a non-empty expression: CtDraw's register skips a
        # vertex whose VERTEX_VERTEXP<n> is empty (the value's first byte
        # must be non-zero), and its page load then takes that vertex's keys out of the
        # bag
        verts = []
        for k, v in bag:
            m = _VERT_RE.match(k.upper())
            if m and v != "":
                verts.append((int(m.group(1)), v))
        for n, expr in verts:
            out["pgvertex"].append(
                {
                    "PAGE": page_name,
                    "INDEX": str(n),
                    "AN": str(an),
                    "EXPR": expr,
                    "X1": d.get("VERTEX_VERTFROMX%d" % n, ""),
                    "Y1": d.get("VERTEX_VERTFROMY%d" % n, ""),
                    "X2": d.get("VERTEX_VERTTOX%d" % n, ""),
                    "Y2": d.get("VERTEX_VERTTOY%d" % n, ""),
                }
            )
    _VALUE_BAGS[pkey] = memo[1]
    while len(_VALUE_BAGS) > _VALUE_BAG_PAGES:
        _VALUE_BAGS.pop(next(iter(_VALUE_BAGS)))
    return out


# --------------------------------------------------------------------------- activex + pglang
def ole_clsid(body: bytes) -> str:
    """The CLSID of the OLE storage embedded in an ActiveX record ('' if none)."""
    k = body.find(_CFB_SIG)
    if k < 0 or len(body) < k + 512:
        return ""
    b = body[k:]
    try:
        ssz = 1 << struct.unpack_from("<H", b, 30)[0]
        d = (struct.unpack_from("<I", b, 48)[0] + 1) * ssz
        raw = b[d + 80 : d + 96]
    except struct.error:
        return ""
    if len(raw) != 16:
        return ""
    return "{%s}" % str(uuid.UUID(bytes_le=raw)).upper()


def activex_rows(ctg, page_name: str) -> List[Dict[str, str]]:
    """The page's activex.DBF rows, in AN order."""
    objs, _par = _records(_parse(ctg))
    out = []
    for po in objs:
        if po.type_code != ACTIVEX_TC:
            continue
        an = _an(po)
        if an is None:
            continue
        name = ""
        for k, v in _value_bag(po):
            if k.upper().startswith("ACTIVEXEXT_") and k.upper().endswith("NAME"):
                name = v
                break
        out.append({"PAGE": page_name, "AN": str(an), "OBJ_NAME": name, "OBJ_GUID": ole_clsid(po.body_raw)})
    out.sort(key=lambda r: int(r["AN"]))
    return out


def natives(s: str) -> List[str]:
    """The @(native) strings of s: the text up to
    ')' or ',' with '^' escaping the next character; an unterminated label ends
    the scan."""
    out: List[str] = []
    i, n = 0, len(s)
    while i < n:
        if s.startswith("@(", i):
            j, buf = i + 2, []
            while j < n and s[j] not in "),":
                c = s[j]
                if c == "^" and j + 1 < n:
                    j += 1
                    c = s[j]
                buf.append(c)
                j += 1
            if j >= n:
                return out
            t = "".join(buf)
            if t and t not in out:
                out.append(t)
            i = j + 1
        else:
            i += 1
    return out


def lang_case_sensitive() -> bool:
    """``[LANGUAGE] CaseSensitive`` of citect.ini: the pglang rows and their index compare
    the native text with case (the index carries the case flag 0x800). Default off."""
    try:
        from .citect_config import ini_value

        v = (ini_value("LANGUAGE", "CaseSensitive", "0") or "0").strip()
    except Exception:  # noqa: BLE001  # swallow-ok: no ini = the default
        return False
    return v not in ("", "0")


def lang_strings(ctg, page_name: Optional[str] = None) -> List[str]:
    """The native strings a save of this page registers in pglang.DBF (distinct without
    case, or with case under [LANGUAGE] CaseSensitive). ``page_name`` = the page whose
    page_rows just read these records: the value bags it kept are read, not walked again."""
    from ..format import geometry as G

    objs, _par = _records(_parse(ctg))
    memo = None
    if page_name is not None:
        memo = value_bag_memo(page_name)
    out: List[str] = []
    seen = set()
    fold = (lambda x: x) if lang_case_sensitive() else (lambda x: x.lower())
    for po in objs:
        if po.type_code != 0x05:
            continue
        srcs = []
        t = G.parse_text_tail(po)
        if t and t.get("text"):
            srcs.append(t["text"])
        for k, v in _value_bag(po, memo):
            K = k.upper()
            if K in ("TXT_OFF", "TXT_ON", "TXT_STR") or K.startswith("STATE_") or K.startswith("ARRAY_"):
                srcs.append(v)
        for src in srcs:
            for x in natives(src):
                x = x.strip(" \t\r\n\x0c\x0b")  # CtDraw: ASCII isspace only
                if x and len(x.encode(CODEC, "replace")) <= LANG_MAX and fold(x) not in seen:
                    seen.add(fold(x))
                    out.append(x)
    return out


# --------------------------------------------------------------------------- register
def _journaled(what: str):
    """Run the decorated database update as ONE journaled unit
    (format/journal): an error half-way puts every table it touched back."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def run(project_dir, *a, **k):
            from ..format import journal

            with journal.transaction(
                "%s in %s" % (what, os.path.basename(os.path.normpath(str(project_dir))))
            ):
                return fn(project_dir, *a, **k)

        return run

    return deco


def find_file(folder: str, name: str) -> Optional[str]:
    """The file ``name`` in ``folder`` whatever its case ('pages.DBF' or 'pages.dbf'), or
    None."""
    p = os.path.join(folder, name)
    if os.path.exists(p):
        return p
    low = name.lower()
    try:
        for f in os.listdir(folder):
            if f.lower() == low:
                return os.path.join(folder, f)
    except OSError:
        pass
    return None


_find_file = find_file


def _open_ndx(folder: str, table: str, writable: bool = True, case: bool = False):
    """The table's index (None: none or unusable). ``writable=False`` for a
    lookup that writes nothing: a plain read handle on the live project. ``case``: its
    keys compare with case (pglang under [LANGUAGE] CaseSensitive)."""
    from ..format.dbf import Ndx

    p = _find_file(folder, table + ".NDX")
    if p is None or os.path.getsize(p) < 1024:
        return None
    try:
        return Ndx(p, writable=writable, case=case)
    except ValueError, OSError:
        return None


def _page_records(dbf, ndx, field: str, value: str) -> List[Tuple[int, bytes]]:
    """(record index, bytes) of every record whose ``field`` equals ``value``
    case-insensitively: through the index when it is keyed on that field, else
    a scan of the field column."""
    if ndx is not None and ndx.expr.upper() == field.upper():
        want = value.lower()
        out = []
        found = [rec - 1 for rec in ndx.find(value)]
        got = _read_records(dbf, [i for i in found if 0 <= i < dbf.nrec])
        for i in found:
            if 0 <= i < dbf.nrec:
                b = got[i]
                if dbf.field(b, field).lower() == want:
                    out.append((i, b))
        return out
    return dbf.find(field, value)


def _read_records(dbf, indices) -> Dict[int, bytes]:
    """``{i: dbf.read(i)}`` for ``indices``, each run of consecutive records read at once."""
    out: Dict[int, bytes] = {}
    rl = dbf.rlen
    idx = sorted(set(indices))
    k = 0
    while k < len(idx):
        j = k
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        first, n = idx[k], j - k + 1
        dbf.fh.seek(dbf._off(first))
        blob = dbf.fh.read(n * rl)
        for m in range(n):
            out[first + m] = blob[m * rl : (m + 1) * rl]
        k = j + 1
    return out


def _row_key(dbf, rec: bytes, seen: Dict[tuple, int]) -> tuple:
    """A row's identity within its page: AN (+ INDEX for pgvertex) and the
    occurrence number -- several input records (Touch / Keyboard) share their
    owner's AN and are matched in order, like CtDraw's walk of the owner's
    children."""
    k = tuple(dbf.field(rec, f) for f in ("AN", "INDEX") if f in dbf.fmap)
    n = seen.get(k, 0)
    seen[k] = n + 1
    return k + (n,)


def _match_rows(dbf, old: List[Tuple[int, bytes]], new: List[bytes]):
    """Match a page's existing register rows with its objects."""
    seen: Dict[tuple, int] = {}
    want = {}
    order = []
    for rec in new:
        k = _row_key(dbf, rec, seen)
        want[k] = rec
        order.append(k)
    seen = {}
    done = set()
    upd, dele = [], []
    for i, rec in old:
        k = _row_key(dbf, rec, seen)
        if k in want and k not in done:
            done.add(k)
            if rec[1:] != want[k][1:]:
                upd.append((i, want[k]))
        else:
            dele.append(i)
    app = [want[k] for k in order if k not in done]
    return upd, dele, app


def _append(dbf, ndx, recs: List[bytes]) -> List[int]:
    first = dbf.append(recs)
    idx = list(range(first, first + len(recs)))
    if ndx is not None and ndx.expr.upper() in dbf.fmap:
        ndx.insert_many((dbf.field(r, ndx.expr), i + 1) for i, r in zip(idx, recs))
    return idx


def _set_comp_state(path: str, value: bytes) -> Optional[str]:
    """Patch PROJECT.CIT's CompState stream in place (a mini-stream stream:
    'COMPILED' -> 'UNCOMPILED' stays inside its 64-byte mini sector).
    Returns the previous value, or None when the file cannot be patched."""
    from ..format import journal

    with journal.open_patch(path) as fh:
        b = fh.read()
        if b[:8] != _CFB_SIG:
            return None
        ssz = 1 << struct.unpack_from("<H", b, 30)[0]
        mssz = 1 << struct.unpack_from("<H", b, 32)[0]
        nfat = struct.unpack_from("<I", b, 44)[0]
        dir_start = struct.unpack_from("<I", b, 48)[0]
        cutoff = struct.unpack_from("<I", b, 56)[0]
        minifat_start = struct.unpack_from("<I", b, 60)[0]
        difat = [x for x in struct.unpack_from("<109I", b, 76) if x < 0xFFFFFFFA][:nfat]

        def off(sec):
            return (sec + 1) * ssz

        fat: List[int] = []
        for sec in difat:
            fat += struct.unpack_from("<%dI" % (ssz // 4), b, off(sec))

        def chain(start, table):
            out, seen = [], set()
            while start < 0xFFFFFFFA and start < len(table) and start not in seen:
                seen.add(start)
                out.append(start)
                start = table[start]
            return out

        ents = [off(sec) + j * 128 for sec in chain(dir_start, fat) for j in range(ssz // 128)]
        if not ents:
            return None
        root_start = struct.unpack_from("<I", b, ents[0] + 116)[0]
        for eo in ents:
            nl = struct.unpack_from("<H", b, eo + 64)[0]
            if b[eo + 66] != 2 or nl < 2:
                continue
            if b[eo : eo + nl - 2].decode("utf-16-le", "replace") != "CompState":
                continue
            start, size = struct.unpack_from("<II", b, eo + 116)
            if size >= cutoff or len(value) >= cutoff:
                return None
            minifat: List[int] = []
            for sec in chain(minifat_start, fat):
                minifat += struct.unpack_from("<%dI" % (ssz // 4), b, off(sec))
            ministream = chain(root_start, fat)
            spans = []
            for ms in chain(start, minifat):
                sec_i, within = divmod(ms * mssz, ssz)
                if sec_i >= len(ministream):
                    return None
                spans.append(off(ministream[sec_i]) + within)
            old = b"".join(b[so : so + mssz] for so in spans)[:size]
            if old == value:
                return old.decode("latin-1")
            if not spans or len(value) > len(spans) * mssz:
                return None
            data = value.ljust(len(spans) * mssz, b"\0")
            for k, so in enumerate(spans):
                fh.seek(so)
                fh.write(data[k * mssz : (k + 1) * mssz])
            fh.seek(eo + 120)
            fh.write(struct.pack("<I", len(value)))
            return old.decode("latin-1")
    return None


def export_page_rows(project_dir: str, page_name: str) -> Dict[str, List[Dict[str, str]]]:
    """The page's live rows in every page table."""
    from ..format import projectlock

    with projectlock.project(project_dir):
        return _export_page_rows(project_dir, page_name)


def _export_page_rows(project_dir: str, page_name: str) -> Dict[str, List[Dict[str, str]]]:
    from ..format.dbf import Dbf

    out: Dict[str, List[Dict[str, str]]] = {}
    page = page_name
    pp = _find_file(project_dir, "pages.DBF")
    if pp:
        with Dbf(pp, writable=False) as pages:
            live = [r for _i, r in pages.find("NAME", page_name) if r[:1] != b"*"]
            if live:
                page = pages.field(live[-1], "NAME")
    for table in PAGE_TABLES + ("activex",):
        path = _find_file(project_dir, table + ".DBF")
        if path is None:
            continue
        with Dbf(path, writable=False) as dbf:
            if "PAGE" not in dbf.fmap:
                continue
            ndx = _open_ndx(project_dir, table, writable=False)
            try:
                recs = sorted(_page_records(dbf, ndx, "PAGE", page))
            finally:
                if ndx is not None:
                    ndx.close()
            rows = []
            for i, r in recs:
                if r[:1] == b"*":
                    continue
                row = dbf.decode(r)
                row["_record"] = str(i + 1)
                rows.append(row)
        if rows:
            out[table] = rows
    return out


def restore_page_rows(
    project_dir: str,
    page_name: str,
    tables: Dict[str, List[Dict[str, str]]],
    *,
    when: Optional[_dt.date] = None,
) -> dict:
    """Put a page's rows back from a backup."""
    from ..format import journal, projectlock
    from ..format.dbf import Dbf

    rep: dict = {}
    with projectlock.project(project_dir), journal.transaction("restore the rows of page %s" % page_name):
        wrote = False
        for table in PAGE_TABLES + ("activex",):
            path = _find_file(project_dir, table + ".DBF")
            if path is None:
                continue
            back = tables.get(table) or []
            dbf = Dbf(path)
            ndx = _open_ndx(project_dir, table)
            try:
                if "PAGE" not in dbf.fmap:
                    continue
                cur = {i: r for i, r in _page_records(dbf, ndx, "PAGE", page_name) if r[:1] != b"*"}
                if not back and not cur:
                    continue
                n_upd = n_app = 0
                appends = []
                for row in back:
                    enc = dbf.encode({k: v for k, v in row.items() if k != "_record"})
                    try:
                        i = int(row.get("_record") or 0) - 1
                    except ValueError:
                        i = -1
                    if 0 <= i < dbf.nrec:
                        old = dbf.read(i)
                        if dbf.field(old, "PAGE").lower() == page_name.lower():
                            if old != enc:
                                dbf.write(i, enc)
                                n_upd += 1
                            cur.pop(i, None)
                            continue
                    appends.append(enc)
                if appends:
                    _append(dbf, ndx, appends)
                    n_app = len(appends)
                for i in sorted(cur):
                    dbf.delete(i)
                if n_upd or n_app or cur:
                    dbf.touch(when)
                    wrote = True
                    rep[table] = {"rewritten": n_upd, "appended": n_app, "deleted": len(cur)}
            finally:
                if ndx is not None:
                    ndx.close()
                dbf.close()
        if wrote:
            recno = None
            pp = _find_file(project_dir, "pages.DBF")
            if pp:
                with Dbf(pp, writable=False) as pages:
                    live = [i for i, r in pages.find("NAME", page_name) if r[:1] != b"*"]
                    recno = live[-1] + 1 if live else None
            if recno is not None:
                log_changes(
                    project_dir, [{"NAME": "pages", "KEY": page_name, "RECORD_NO": str(recno)}], when=when
                )
            mark_uncompiled(project_dir)
    return rep


def register_page(
    project_dir: str,
    page_name: str,
    ctg,
    resolver=None,
    *,
    when: Optional[_dt.date] = None,
    log_change: bool = True,
    mark_uncompiled: bool = True,
    languages: bool = True,
    require_page_row: bool = True,
) -> dict:
    """Write a page's register rows into the project's databases."""
    from ..format import journal, projectlock

    parsed = _parse(ctg)  # (outside the lock: the page alone)
    with (
        projectlock.project(project_dir),
        journal.transaction(
            "register page %s in %s" % (page_name, os.path.basename(os.path.normpath(project_dir)))
        ) as txn,
    ):
        rep = _register_page(
            project_dir,
            page_name,
            parsed,
            when=when,
            log_change=log_change,
            mark_uncompiled=mark_uncompiled,
            languages=languages,
            require_page_row=require_page_row,
        )
    rep["files"] = list(txn.written)
    return rep


def _register_page(
    project_dir: str,
    page_name: str,
    ctg,
    *,
    when: Optional[_dt.date] = None,
    log_change: bool = True,
    mark_uncompiled: bool = True,
    languages: bool = True,
    require_page_row: bool = True,
) -> dict:
    from ..format.dbf import Dbf

    t0 = time.perf_counter()
    parsed = _parse(ctg)
    rep: dict = {
        "page": page_name,
        "tables": {},
        "activex": [],
        "pglang": [],
        "changes": [],
        "compstate": None,
    }
    opened: list = []

    def track(x):
        if x is not None:
            opened.append(x)
        return x

    try:
        # the PAGE key is the page name AS OPENED (the file's spelling): CtDraw's row
        # builder copies the page object's own name and never reads pages.DBF.
        # The pages.DBF row is found without case.
        page, pages_recno = page_name, None
        pp = _find_file(project_dir, "pages.DBF")
        pages = track(Dbf(pp)) if pp else None
        if pages is not None:
            live = [(i, r) for i, r in pages.find("NAME", page_name) if r[:1] != b"*"]
            if live:
                i, r = live[-1]
                pages_recno = i + 1
        rep["page"], rep["pages_recno"] = page, pages_recno
        if pages is not None and pages_recno is None and require_page_row:
            rep["unregistered"] = True
            return rep

        rows = page_rows(parsed, page)
        wrote = False
        tables_written = 0
        for table in PAGE_TABLES:
            new = rows[table]
            path = _find_file(project_dir, table + ".DBF")
            if path is None:
                rep["tables"][table] = {"action": "missing_table" if new else "absent", "new": len(new)}
                continue
            dbf = track(Dbf(path))
            ndx = track(_open_ndx(project_dir, table))
            has_an = "AN" in dbf.fmap
            old = [
                (i, r)
                for i, r in _page_records(dbf, ndx, "PAGE", page)
                if r[:1] != b"*" and (not has_an or dbf.field(r, "AN"))
            ]
            old.sort()
            plan = _match_rows(dbf, old, dbf.encode_many(new))
            upd, dele, app = plan
            for i, rec in upd:
                dbf.write(i, rec)
            for i in dele:
                dbf.delete(i)
            idx = _append(dbf, ndx, app)
            changed = bool(upd or dele or app)
            if changed:
                dbf.touch(when)
                wrote = True
                tables_written += 1
                kill_point("in_register:%d" % tables_written)
            rep["tables"][table] = {
                "action": "updated" if changed else "unchanged",
                "old": len(old),
                "new": len(new),
                "updated": [i + 1 for i, _r in upd],
                "deleted": [i + 1 for i in dele],
                "appended": [i + 1 for i in idx],
            }

        # activex: keyed (PAGE, AN) -- rewrite changed rows, append new ones
        ax_changes: List[int] = []
        ax_new = activex_rows(parsed, page)
        axp = _find_file(project_dir, "activex.DBF")
        if ax_new and axp:
            ax = track(Dbf(axp))
            axn = track(_open_ndx(project_dir, "activex"))
            have = {}
            for i, r in sorted(_page_records(ax, axn, "PAGE", page)):
                if r[:1] == b"*":
                    continue
                a = ax.field(r, "AN")
                if not a:
                    ax.delete(i)
                    ax_changes.append(i + 1)
                    rep["activex"].append({"AN": "", "action": "deleted", "record": i + 1})
                elif a not in have:
                    have[a] = (i, r)
            appends = []
            for row in ax_new:
                enc = ax.encode(row)
                cur = have.get(row["AN"])
                if cur is None:
                    appends.append(enc)
                elif cur[1][1:] != enc[1:]:
                    ax.write(cur[0], enc)
                    ax_changes.append(cur[0] + 1)
                    rep["activex"].append({"AN": row["AN"], "action": "updated", "record": cur[0] + 1})
            for i, enc in zip(_append(ax, axn, appends), appends):
                ax_changes.append(i + 1)
                rep["activex"].append({"AN": ax.field(enc, "AN"), "action": "appended", "record": i + 1})
            if ax_changes:
                ax.touch(when)
                wrote = True

        # pglang: project-wide native strings, append the missing ones
        lp = _find_file(project_dir, "pglang.DBF") if languages else None
        if lp:
            strs = lang_strings(parsed, page)
            if strs:
                lg = track(Dbf(lp))
                exact = lang_case_sensitive()
                lgn = track(_open_ndx(project_dir, "pglang", case=exact))
                add = [
                    lg.encode({"NATIVE": x})
                    for x in strs
                    if not [
                        r
                        for r in _page_records(lg, lgn, "NATIVE", x)
                        if not exact or lg.field(r[1], "NATIVE") == x
                    ]
                ]
                if add:
                    _append(lg, lgn, add)
                    lg.touch(when)
                    wrote = True
                    rep["pglang"] = [lg.field(a, "NATIVE") for a in add]

        # the change log (pages row + activex rows) and the pages.DBF date
        if log_change:
            entries = []
            if pages_recno is not None:
                entries.append({"NAME": "pages", "KEY": page, "RECORD_NO": str(pages_recno)})
            entries += [{"NAME": "activex", "KEY": page, "RECORD_NO": str(r)} for r in ax_changes]
            cp = _find_file(project_dir, "changes.DBF")
            if cp and entries:
                ch = track(Dbf(cp))
                chn = track(_open_ndx(project_dir, "changes"))
                _append(ch, chn, [ch.encode(e) for e in entries])
                ch.touch(when)
                rep["changes"] = entries
                wrote = True
            if pages is not None and pages_recno is not None:
                pages.touch(when)

        if mark_uncompiled and wrote:
            cit = _find_file(project_dir, "PROJECT.CIT")
            if cit:
                prev = _set_comp_state(cit, b"UNCOMPILED")
                rep["compstate"] = {"previous": prev, "now": "UNCOMPILED" if prev is not None else None}
    finally:
        errors = []
        for x in opened:
            try:
                x.close()
            except OSError as e:  # a lost write must not pass silently
                errors.append("%s: %s" % (x.path, e))
        if errors:
            raise OSError("register_page: " + "; ".join(errors))
    rep["seconds"] = round(time.perf_counter() - t0, 3)
    return rep


# --------------------------------------------------------------------------- page lifecycle
def page_tables(project_dir: str) -> List[Tuple[str, str]]:
    """(table, path) of every per-page table in the project."""
    from ..format.dbf import Dbf

    out = []
    try:
        names = sorted(os.listdir(project_dir), key=str.lower)
    except OSError:
        return out
    for fn in names:
        stem, ext = os.path.splitext(fn)
        low = stem.lower()
        if ext.lower() != ".dbf" or not (low.startswith("pg") or low == "activex"):
            continue
        path = os.path.join(project_dir, fn)
        try:
            with Dbf(path, writable=False) as d:
                if "PAGE" in d.fmap:
                    out.append((low, path))
        except OSError, ValueError, struct.error:
            continue
    return out


@_journaled("delete page rows")
def pack_project_tables(project_dir: str, progress=None) -> dict:
    """Remove the deleted rows of every table of the project and build their indexes again,
    as Studio's Pack does. A table the change log (changes.DBF) names keeps its record
    numbers until the next compile. ``progress(i, n, table)`` returning False stops between
    tables. Returns {"packed": {table: rows removed}, "saved": bytes, "kept": [tables],
    "failed": [(table, why)]}."""
    from ..format import projectlock
    from ..format.dbf import pack_table
    from ..format.projects import read_dbf_rows

    rep: dict = {"packed": {}, "saved": 0, "kept": [], "failed": []}
    try:
        names = sorted(f for f in os.listdir(project_dir) if f.lower().endswith(".dbf"))
    except OSError as e:
        rep["failed"].append(("", e.strerror or str(e)))
        return rep
    ch = _find_file(project_dir, "changes.DBF")
    logged = {str(r.get("NAME") or "").strip().lower() for r in (read_dbf_rows(ch) if ch else ())}
    with projectlock.project(project_dir):
        for i, fn in enumerate(names):
            table = os.path.splitext(fn)[0]
            if progress is not None and progress(i, len(names), table) is False:
                break
            if table.lower() in logged:
                rep["kept"].append(table)
                continue
            path = os.path.join(project_dir, fn)
            ndx = _find_file(project_dir, table + ".NDX")
            idx = [ndx] if ndx is not None and os.path.getsize(ndx) >= 1024 else []
            case = False
            if idx:
                with open(idx[0], "rb") as fh:
                    key = fh.read(244)[24:244].split(b"\0")[0].strip().upper()
                case = key == b"NATIVE" and lang_case_sensitive()
            before = os.path.getsize(path) + sum(os.path.getsize(x) for x in idx)
            try:
                n = pack_table(path, idx, case=case)
            except (OSError, ValueError) as e:
                rep["failed"].append((table, getattr(e, "strerror", None) or str(e)))
                continue
            if n:
                rep["packed"][table] = n
                rep["saved"] += before - os.path.getsize(path) - sum(os.path.getsize(x) for x in idx)
    return rep


def rename_page_rows(project_dir: str, renames: Dict[str, str], *, when: Optional[_dt.date] = None) -> dict:
    """Give the rows of pages ``renames`` ({old lower case: new}) in every page table and
    activex their new names in place, each table opened once and its index built again;
    {table: rows renamed}."""
    from ..format.dbf import Dbf, rebuild_ndx

    rep: dict = {}
    for table in PAGE_TABLES + ("activex",):
        path = _find_file(project_dir, table + ".DBF")
        if path is None:
            continue
        with Dbf(path) as dbf:
            if "PAGE" not in dbf.fmap:
                continue
            ndx = _open_ndx(project_dir, table, writable=False)
            try:
                hits = [
                    (i, new)
                    for old, new in renames.items()
                    for i, r in _page_records(dbf, ndx, "PAGE", old)
                    if r[:1] != b"*"
                ]
            finally:
                if ndx is not None:
                    ndx.close()
            if not hits:
                continue
            for i, new in hits:
                dbf.set_field(i, "PAGE", new)
            dbf.touch(when)
            if ndx is not None:
                rebuild_ndx(dbf, ndx.path, case=table == "pglang" and lang_case_sensitive())
            rep[table] = len(hits)
    return rep


def delete_page_rows(
    project_dir: str, page_name: str, *, when: Optional[_dt.date] = None
) -> Dict[str, List[int]]:
    """Flag every row of a page deleted in the per-page tables."""
    from ..format import projectlock
    from ..format.dbf import Dbf

    rep: Dict[str, List[int]] = {}
    with projectlock.project(project_dir):
        for table, path in page_tables(project_dir):
            dbf = Dbf(path)
            ndx = _open_ndx(project_dir, table)
            try:
                rows = sorted(i for i, r in _page_records(dbf, ndx, "PAGE", page_name) if r[:1] != b"*")
                for i in rows:
                    dbf.delete(i)
                if rows:
                    dbf.touch(when)
                    rep[table] = [i + 1 for i in rows]
            finally:
                if ndx is not None:
                    ndx.close()
                dbf.close()
    return rep


def page_keyboard_rows(project_dir: str, page_name: str) -> List[Dict[str, str]]:
    """A page's page-wide keyboard commands."""
    from ..format import projectlock
    from ..format.dbf import Dbf

    path = _find_file(project_dir, "pgkey.DBF")
    if path is None:
        return []
    with projectlock.project(project_dir), Dbf(path, writable=False) as d:
        ndx = _open_ndx(project_dir, "pgkey", writable=False)
        try:
            return [
                d.decode(r)
                for _i, r in sorted(_page_records(d, ndx, "PAGE", page_name))
                if r[:1] != b"*" and not d.field(r, "AN")
            ]
        finally:
            if ndx is not None:
                ndx.close()


@_journaled("write page keyboard commands")
def set_page_keyboard_rows(
    project_dir: str, page_name: str, rows: List[Dict[str, str]], *, when: Optional[_dt.date] = None
) -> int:
    """Replace a page's page-wide keyboard commands."""
    from ..format.dbf import Dbf

    path = _find_file(project_dir, "pgkey.DBF")
    if path is None:
        return 0
    dbf = Dbf(path)
    ndx = _open_ndx(project_dir, "pgkey")
    try:
        old = sorted(
            (i, r)
            for i, r in _page_records(dbf, ndx, "PAGE", page_name)
            if r[:1] != b"*" and not dbf.field(r, "AN")
        )
        new = [dbf.encode(dict(r, PAGE=page_name, AN="")) for r in rows]
        # a row read back through decode() lost trailing blanks / line breaks
        # of its fields: when the n-th stored row holds the same values, keep
        # its bytes as they are
        for j, rec in enumerate(new):
            if j < len(old) and dbf.decode(old[j][1]) == dbf.decode(rec):
                new[j] = old[j][1]
        upd, dele, app = _match_rows(dbf, old, new)
        for i, rec in upd:
            dbf.write(i, rec)
        for i in dele:
            dbf.delete(i)
        _append(dbf, ndx, app)
        if upd or dele or app:
            dbf.touch(when)
        return len(new)
    finally:
        if ndx is not None:
            ndx.close()
        dbf.close()


@_journaled("log changes")
def log_changes(project_dir: str, entries: List[Dict[str, str]], *, when: Optional[_dt.date] = None) -> bool:
    """Append Project Editor change-log entries (NAME table, KEY page,
    RECORD_NO) to ``changes.DBF`` (what an incremental compile rebuilds).
    False when the project has no change log."""
    from ..format.dbf import Dbf

    cp = _find_file(project_dir, "changes.DBF")
    if not cp or not entries:
        return False
    ch = Dbf(cp)
    chn = _open_ndx(project_dir, "changes")
    try:
        _append(ch, chn, [ch.encode(e) for e in entries])
        ch.touch(when)
    finally:
        if chn is not None:
            chn.close()
        ch.close()
    return True


def mark_uncompiled(project_dir: str) -> Optional[str]:
    """PROJECT.CIT CompState -> UNCOMPILED; the previous state, or None."""
    cit = _find_file(project_dir, "PROJECT.CIT")
    return _set_comp_state(cit, b"UNCOMPILED") if cit else None


def register_files(project_dir: str) -> List[str]:
    """The files of ``project_dir`` a page save's register writes that exist: pages.DBF,
    PROJECT.CIT, each page table (.DBF and .NDX), activex, pglang and changes."""
    out = []
    names = ["pages.DBF", "PROJECT.CIT"]
    for t in PAGE_TABLES + ("activex", "pglang", "changes"):
        names += [t + ".DBF", t + ".NDX"]
    seen = set()
    for n in names:
        p = _find_file(project_dir, n)
        if p is not None and os.path.normcase(p) not in seen:
            seen.add(os.path.normcase(p))
            out.append(p)
    return out


def probe_writable(project_dir: str) -> None:
    """Raise :class:`ProjectDatabaseInUse` naming the first file of the project's register
    (:func:`register_files`) that cannot be opened for writing now (Plant SCADA Studio holds
    it). Nothing is written: each file is opened for update and closed again."""
    for p in register_files(project_dir):
        try:
            open(p, "r+b").close()
        except FileNotFoundError:
            continue  # (gone meanwhile: nothing to write)
        except PermissionError as e:
            raise ProjectDatabaseInUse(p, e.strerror or "") from e


def apply_page_state(
    project_dir: str, name: str, state: dict, *, copy_keys_from=None, proj=None
) -> List[str]:
    """Write a page's staged Page Properties into its project."""
    from ..format import projectlock
    from .project import Project

    proj = proj if proj is not None else Project(project_dir)
    wrote = []
    with projectlock.project(project_dir):
        disk = proj.page_record(name)
        staged = dict(state.get("page_record") or {})
        if staged:
            if disk is None:
                raise PageRecordError(
                    "page %s has no row in pages.DBF, so its page properties could not be written" % name
                )
            pending = {k: v for k, v in staged.items() if (disk.get(k) or "").strip() != v.strip()}
            if pending:
                if not proj.update_page_record(name, pending):
                    raise PageRecordError("the pages.DBF row of page %s could not be updated" % name)
                wrote.append("pages.DBF")
        pname = (disk or {}).get("NAME") or name
        keys = state.get("page_keys")
        if keys is not None:
            if _find_file(project_dir, "pgkey.DBF") is None:
                if keys:
                    raise PageRecordError(
                        "project %s has no pgkey.DBF, so the page "
                        "keyboard commands could not be written"
                        % os.path.basename(os.path.normpath(project_dir))
                    )
            else:
                set_page_keyboard_rows(project_dir, pname, keys)
                wrote.append("pgkey.DBF")
        elif copy_keys_from is not None:
            proj.copy_page_keyboard_rows(copy_keys_from[0], copy_keys_from[1], name)
    return wrote


from ..infra import caches as _caches  # noqa: E402

_caches.register("pgregister.value_bags", _VALUE_BAGS, on=("refresh", "tests"))
_caches.register("pgregister.wanted_rows", _WANT, on=("refresh", "tests"))
