"""The page model ctgedit sends the simulator (``ctsim-page/0``)."""

from __future__ import annotations

import collections
import json
import math
import os
import struct
from typing import Dict, List, Optional, Tuple

from ..format import colourstate as CS
from ..format import geometry as G
from ..format import placement as P
from ..format import symbolset as SS
from ..format.symbols import reference_from_body as _reference_from_body
from ..format.dbf import ASCII_SAME
from ..format.textcodec import CODEC
from ..format import codes as _codes
from ..infra.logsetup import note_exception as _note_exc
from ..format.inputs import parents as _parents

FORMAT = "ctsim-page/0"

TOUCH_TC, KEY_TC = _codes.TC.TOUCH, _codes.TC.KEYBOARD
INPUT_TCS = frozenset({TOUCH_TC, KEY_TC})
TEXT_TC, SET_TC, BUTTON_TC, TREND_TC = 0x05, 0x0C, 0x1001, 0x1003
LINE_TC, PIPE_TC = 0x01, 0x0B
#: polyline classes whose class section starts with the u16-counted vertex array
COUNTED_NODE_TCS = frozenset({0x10, PIPE_TC})
#: other node classes (read with geometry.get_object_nodes)
OTHER_NODE_TCS = frozenset({0x0A, 0x0E})
METADATA_FLAG = 0x100

# class names (CtDraw's type code table; ui/objprops/registry.CLASS_NAMES)
CLASS_NAMES: Dict[int, str] = {
    0x01: "line",
    0x03: "rectangle",
    0x04: "ellipse",
    0x05: "text",
    0x06: "sparkle",
    0x07: "symbol",
    0x08: "group",
    0x09: "metafile",
    0x0A: "polygon",
    0x0B: "pipe",
    0x0C: "symbolSet",
    0x0D: "activex",
    0x0E: "freehand",
    0x0F: "rectangle",
    0x10: "polygon",
    0x11: "ellipse",
    0x13: "webContent",
    0x1000: "anPoint",
    0x1001: "button",
    0x1002: "bar",
    0x1003: "trend",
    0x1004: "number",
    0x1005: "string",
    0x1006: "digitalText",
    0x1007: "legacySymbol",
    0x1008: "colourFlood",
    0x1009: "cicodeObject",
    _codes.TC.KEYBOARD: "keyboard",
    0x100D: "animatedSymbol",
    0x100E: "sizeMove",
    0x100F: "slider",
    _codes.TC.TOUCH: "touch",
    0xFFFF: "dummy",
}


# --------------------------------------------------------------------------- small readers
def _lp(b: bytes, o: int) -> Tuple[str, int]:
    (n,) = struct.unpack_from("<H", b, o)
    if o + 2 + n > len(b):
        raise struct.error("lpstr overruns the body")
    return b[o + 2 : o + 2 + n].decode(CODEC), o + 2 + n


def _pair(b: bytes, o: int) -> List[int]:
    on, off = struct.unpack_from("<II", b, o)
    return [on, off]


def _an(po) -> int:
    """handle + 1; -1 without a valid handle (geometry.live_an)."""
    try:
        return G.live_an(po)
    except Exception:  # noqa: BLE001 -- a short body: no handle
        _note_exc("pagemodel._an")
        return -1


# --------------------------------------------------------------------------- per record
def _slots(po):
    (s1, s2, s3), _end = P.read_slots_of(po)
    return [(s[0], s[1]) for s in (s1, s2, s3)]


def _axis_lengths(mode: int, m6) -> Tuple[float, float]:
    """|T(1, 0)| and |T(0, 1)| of a slot's linear part (mode 0 / 1: none)."""
    a, b, c, d, _h, _k = m6
    if mode in (0, 1):
        return 1.0, 1.0
    return math.hypot(a, b), math.hypot(c, d)


def _magnitude(slots) -> Optional[List[float]]:
    """slot 3 / slot 2 lengths per axis (the runtime; a guess for which
    slots the drawing object's transforms are). None = (1, 1)."""
    (m2, x2), (m3, x3) = slots[1], slots[2]
    if m2 in (0, 1) and m3 in (0, 1):
        return None
    l2, l3 = _axis_lengths(m2, x2), _axis_lengths(m3, x3)
    out = []
    for a, b in zip(l3, l2):
        out.append(a / b if b else 1.0)
    if all(abs(v - 1.0) < 1e-9 for v in out):
        return None
    return [round(v, 12) for v in out]


def decode_colour(po) -> Optional[dict]:
    """The colour-state array as the loader reads it (CtDraw): None when the
    record does not walk. ``maxon`` = the COL_MAXON the loader ends with: bit 0x20's pair
    is written back into COL_MAXON after bit 0x02's (colourstate.py); the bit
    0x02 pair is kept as ``maxonFirst`` only when the two differ."""
    L = CS.layout(po)
    if L is None:
        return None
    b = po.body_raw
    flag = L["flag"]
    out: dict = {"flag": flag, "state0": _pair(b, L["state0_at"])}
    first = _pair(b, L["maxon_at"]) if flag & 0x02 else None
    second = _pair(b, L["x20_at"]) if flag & 0x20 else None
    maxon = second if second is not None else first
    if maxon is not None:
        out["maxon"] = maxon
    if first is not None and second is not None and first != second:
        out["maxonFirst"] = first
    if flag & 0x04:
        out["sta"] = [_pair(b, L["sta_at"] + 8 * i) for i in range(L["sta_n"])]
    if flag & 0x08:
        out["arr"] = [_pair(b, L["arr_at"] + 8 * i) for i in range(L["arr_n"])]
    if flag & 0x10:
        thr = []
        for i in range(L["thr_n"]):
            f, pct, on, off = struct.unpack_from("<BhII", b, L["thr_at"] + 11 * i)
            thr.append([f, pct, on, off])
        out["thr"] = thr
    if flag & 0x40:
        out["filcol"] = _pair(b, L["x40_at"])
    return out


def encode_colour(c: dict) -> bytes:
    """The colour-state array bytes of a :func:`decode_colour` dict (the round trip)."""
    flag = int(c["flag"])
    out = struct.pack("<I", flag) + struct.pack("<II", *c["state0"])
    maxon = c.get("maxon")
    if flag & 0x02:
        out += struct.pack("<II", *(c.get("maxonFirst") or maxon))
    for bit, key in ((0x04, "sta"), (0x08, "arr")):
        if flag & bit:
            lst = c.get(key) or []
            out += struct.pack("<H", len(lst)) + b"".join(struct.pack("<II", *p) for p in lst)
    if flag & 0x10:
        lst = c.get("thr") or []
        out += struct.pack("<H", len(lst)) + b"".join(
            struct.pack("<BhII", int(f), int(pct), on, off) for f, pct, on, off in lst
        )
    if flag & 0x20:
        out += struct.pack("<II", *maxon)
    if flag & 0x40:
        out += struct.pack("<II", *c["filcol"])
    return out


def _metadata_offset(po, L: dict) -> int:
    """Start of the class section (= of the dwFlags 0x100 metadata section): after the
    colour-state array and the style block."""
    return L["end"] + (54 if po.subtype_size & 0x2 else 0)


def decode_metadata(po) -> Optional[List[List[str]]]:
    """The dwFlags 0x100 metadata section: [[name, value]] with the stored
    '*' value prefix removed. None without the section."""
    if not po.subtype_size & METADATA_FLAG:
        return None
    L = CS.layout(po)
    if L is None:
        return None
    b = po.body_raw
    o = _metadata_offset(po, L)
    (n,) = struct.unpack_from("<H", b, o)
    o += 2
    out = []
    for _ in range(n):
        name, o = _lp(b, o)
        val, o = _lp(b, o)
        out.append([name, val[1:] if val.startswith("*") else val])
    return out


def decode_text(po) -> Optional[dict]:
    """A text record's string section (the start of its class section after the
    metadata): {flag, off, on, states, array}; None when it does not read."""
    try:
        o = P.class_section_offset(po)
        b = po.body_raw
        (flag,) = struct.unpack_from("<I", b, o)
        o += 4
        off, o = _lp(b, o)
        out: dict = {"flag": flag, "off": off, "on": None, "states": [], "array": []}
        if flag & 2:
            out["on"], o = _lp(b, o)
        for bit, key in ((4, "states"), (8, "array")):
            if flag & bit:
                (n,) = struct.unpack_from("<H", b, o)
                o += 2
                lst = []
                for _ in range(n):
                    s, o = _lp(b, o)
                    lst.append(s)
                out[key] = lst
        return out
    except struct.error, UnicodeDecodeError, AttributeError, ValueError, IndexError:
        return None  # AttributeError: a header that does not parse


def encode_text(t: dict) -> bytes:
    """The string section bytes of a :func:`decode_text` dict (the round trip)."""

    def lp(s):
        e = (s or "").encode(CODEC)
        return struct.pack("<H", len(e)) + e

    flag = int(t["flag"])
    out = struct.pack("<I", flag) + lp(t.get("off"))
    if flag & 2:
        out += lp(t.get("on"))
    for bit, key in ((4, "states"), (8, "array")):
        if flag & bit:
            lst = t.get(key) or []
            out += struct.pack("<H", len(lst)) + b"".join(lp(s) for s in lst)
    return out


def _nodes(po) -> Optional[List[List[int]]]:
    """The vertex list in page coordinates of a record with its own appearance."""
    tc = po.type_code
    if not po.subtype_size & 0x2:
        return None
    b = po.body_raw
    try:
        if tc in COUNTED_NODE_TCS:
            o = P.class_section_offset(po)
            (n,) = struct.unpack_from("<H", b, o)
            if o + 2 + 4 * n > len(b):
                return None
            return [list(p) for p in struct.iter_unpack("<2h", b[o + 2 : o + 2 + 4 * n])]
        if tc == LINE_TC:
            # a free line's last 8 bytes: the end points in DRAW order
            return [list(p) for p in struct.iter_unpack("<2h", b[len(b) - 8 :])]
        if tc in OTHER_NODE_TCS:
            got = G.get_object_nodes(po)
            return [list(p) for p in got] if got else None
    except struct.error:
        return None
    return None


def _value_props(po, memo=None) -> Dict[str, str]:
    """The record's level-0 value bag (e0.b1) as {KEY: value} (pgregister's reader).
    ``memo`` = pgregister._value_bag's (the bags the page's rows read, this call's)."""
    from ..io import pgregister as PR

    return {k.upper(): v for k, v in PR._value_bag(po, memo)}


def _text_font(po, tail, anchor=None) -> Optional[dict]:
    """A text's own font and layout point (its style block attribs + the text tail), or None
    without either (a genie member without its own appearance). The layout point is the
    record's anchor in page coordinates (``anchor``, the header's); the tail's anchor echo
    is local to the text's transform and only used without one."""
    if tail is None or not tail.get("face"):
        return None
    try:
        cb = G.find_colour_block(po)
    except struct.error, IndexError, ValueError:
        cb = None
    if cb is None:
        return None
    at = cb.flags[G.FLAG_ATTRIBS]
    echo = anchor or tail.get("echo")
    if not echo:
        return None
    return {
        "face": tail["face"],
        "points": int(tail.get("size") or 0),
        "bold": bool(at & 0x10),
        "italic": bool(at & 0x20),
        "underline": bool(at & 0x40),
        "strikeout": bool(at & 0x80),
        "justify": at & 0x0F,
        "orient": int(tail.get("orient") or 1),
        "anchor": [int(echo[0]), int(echo[1])],
    }


def _link(po) -> Optional[dict]:
    if not po.subtype_size & 0x1:
        return None
    try:
        ref = _reference_from_body(po.body_raw)
    except Exception:  # noqa: BLE001 -- an unreadable link: none
        _note_exc("pagemodel._link")
        ref = None
    if ref is None:
        return None
    return {"library": ref.library, "element": ref.element, "type": (ref.ext or "").lower()}


class _OwnerInfo:
    """The appearance owner of each record (format/ctf._Owners, CtDraw's load linkage): an
    art-stripped genie member takes its element twin's appearance. Used for what a stripped
    record lacks: a symbol-set state's 'library.element' and a polygon's vertex count."""

    def __init__(self, objs, resolver):
        self.owners: List = list(objs)
        self.where: dict = {}
        if resolver is None:
            return
        try:
            from ..format import ctf

            o = ctf._Owners(resolver)
            self.owners = o.resolve(objs)
            self.where = o._where
        except Exception:  # noqa: BLE001 -- no owners: the stripped details stay unknown
            _note_exc("pagemodel.__init__")
            self.owners = list(objs)

    def owner(self, i: int):
        ow = self.owners[i] if i < len(self.owners) else None
        return ow

    def element_name(self, i: int) -> Optional[str]:
        """'library.element' when record i's owner is the ROOT of a resolved element (the
        twin was a linked leaf symbol), else None."""
        ow = self.owner(i)
        loc = self.where.get(id(ow)) if ow is not None else None
        if loc is None or loc[1] != 0:
            return None
        lib, elem, _ext = loc[0]
        return "%s.%s" % (lib, elem)


def _state_name(objs, k: int, owners: _OwnerInfo) -> Optional[str]:
    po = objs[k]
    sym = SS.state_symbol(po)
    if sym is not None:
        return "%s.%s" % (sym[0], sym[1])
    if po.type_code == SS.DUMMY_TC:
        return None
    if po.type_code == SS.STATE_TC and not po.subtype_size & 0x3:
        return owners.element_name(k)  # an art-stripped genie copy
    if SS.state_kind(po) == "stripped" and po.type_code == SS.GROUP_TC:
        # the art-stripped copy of a group-root symbol's instance forest in a placed genie
        # (CtDraw's FxParts save: 0x08 dwFlags 0x18 over the shape): its
        # element twin is the linked root
        ow = owners.owner(k)
        sym = SS.state_symbol(ow) if ow is not None and ow is not po else None
        if sym is not None:
            return "%s.%s" % (sym[0], sym[1])
        return owners.element_name(k)
    return None


def object_summary(objs, i: int, par, kids, owners: _OwnerInfo, notes: list, value_bags=None) -> dict:
    """The drawing summary of record ``i`` (see the module doc). ``value_bags`` = the
    value bags the page's rows read (pgregister._value_bag's memo)."""
    po = objs[i]
    tc, dw = po.type_code, po.subtype_size
    gh = G.parse_geom_header(po.body_raw)
    p = par[i]
    o: dict = {
        "an": _an(po),
        "type": tc,
        "class": CLASS_NAMES.get(tc, "0x%04X" % tc),
        "record": i,
        "parent": _an(objs[p]) if p is not None else 0,
        "flags": dw,
    }
    if tc == 0x0D:
        from ..io.pgregister import ole_clsid

        clsid = ole_clsid(po.body_raw)
        if clsid:
            o["controlClsid"] = clsid
    if gh is not None:
        if gh.name:
            o["name"] = gh.name
        o["rect"] = list(gh.rect1)
        o["bounds"] = list(gh.rect2)
        o["pos"] = list(gh.anchor)
    ch = [c for c in kids.get(i, ()) if objs[c].type_code not in INPUT_TCS]
    o["children"] = [_an(objs[c]) for c in ch]
    try:
        sl = _slots(po)
        if any(m for m, _x in sl):
            o["slots"] = [[m] + [float(v) for v in x] for m, x in sl]
        mag = _magnitude(sl)
        if mag is not None:
            o["magnitude"] = mag
    except struct.error, IndexError:
        notes.append("record %d: transforms do not read" % i)
    lk = _link(po)
    if lk is not None:
        o["link"] = lk
    if any(objs[c].type_code == TOUCH_TC for c in kids.get(i, ())):
        o["hasTouch"] = True
    if any(objs[c].type_code == KEY_TC for c in kids.get(i, ())):
        o["hasKey"] = True
    col = decode_colour(po)
    if col is not None:
        o["colour"] = col
    elif tc != 0xFFFF:
        notes.append("record %d (AN %d): the colour-state array does not walk" % (i, o["an"]))
    try:
        md = decode_metadata(po)
    except struct.error, UnicodeDecodeError:
        md = None
        notes.append("record %d: the metadata section does not read" % i)
    if md:
        o["metadata"] = md
    if tc == TEXT_TC:
        t = decode_text(po)
        if t is not None:
            t["mode"] = SS.atoi(_value_props(po, value_bags).get("TXTMODE", "0"))
            if dw & 0x2:
                tail = G.parse_text_tail(po)
                if tail is not None and tail.get("text") is not None:
                    t["caption"] = tail["text"]
                font = _text_font(po, tail, o.get("pos"))
                if font is not None:
                    t["font"] = font
            o["text"] = t
        else:
            notes.append("record %d (AN %d): the text string section does not read" % (i, o["an"]))
    elif tc == SET_TC:
        cs = SS.class_section(po)
        states = [c for c in ch]
        ss: dict = {
            "states": len(states),
            "names": [_state_name(objs, c, owners) for c in states],
            "kinds": [SS.state_kind(objs[c]) for c in states],
        }
        if cs is not None:
            ss["declared"] = cs["declared"]
            if cs["states"] != len(states):
                ss["storedStates"] = cs["states"]
            if cs["hotspot"] is not None:
                ss["hotspot"] = list(cs["hotspot"])
        ss["mode"] = SS.atoi(_value_props(po, value_bags).get("TXTMODE", "0"))
        o["symbolSet"] = ss
    elif tc == TREND_TC:
        from ..format import legacy

        src = "own"
        try:
            ti = legacy.trend_info(po)
            if ti is None:
                # an art-stripped genie member: the class section (pens, samples) is its
                # element twin's (the .ctF bakes the owner's, ctf.ctf_rec)
                ow = owners.owner(i)
                if ow is not None and ow is not po:
                    ti = legacy.trend_info(ow)
                    src = "element"
        except Exception:  # noqa: BLE001
            _note_exc("pagemodel.object_summary")
            ti = None
        if ti is not None:
            o["trend"] = {
                "samples": ti["samples"],
                "pixelsPerSample": ti["spacing"],
                "height": ti["height"],
                "pens": [list(x) for x in ti["pens"]],
                "from": src,
            }
    elif tc == BUTTON_TC:
        btn: dict = {}
        L = CS.layout(po)
        try:
            if L is not None and "sec80_at" in L:
                btn["caption"], _e = _lp(po.body_raw, L["sec80_at"])
            if dw & 0x40:
                f = P.walk(po).get("font")
                if f is not None:
                    btn["font"] = {
                        "face": f["face"],
                        "charset": f["charset"],
                        "attribs": f["attribs"],
                        "size": f["size"],
                        "text": list(f["text"]),
                        "back": list(f["back"]),
                    }
        except struct.error, UnicodeDecodeError, KeyError:
            notes.append("record %d: the button sections do not read" % i)
        if btn:
            o["button"] = btn
    nodes = _nodes(po)
    if nodes is not None:
        o["vertices"] = nodes
    elif tc in COUNTED_NODE_TCS:
        # an art-stripped genie member has no vertex array of its own: its element twin's
        # count, as a NUMBER (the .ctF bakes the twin's array; the vertex animation needs
        # only the count)
        ow = owners.owner(i)
        if ow is not None and ow is not po:
            got = _nodes(ow)
            if got is not None:
                o["vertices"] = len(got)
    return o


# --------------------------------------------------------------------------- the page
def _page_name(doc) -> str:
    rec = None
    try:
        rec = doc.page_record_on_disk()
    except Exception:  # noqa: BLE001
        _note_exc("pagemodel._page_name")
        rec = None
    if rec and rec.get("NAME"):
        return rec["NAME"]
    return os.path.splitext(os.path.basename(doc.path or ""))[0]


def _rdb_info(path: str) -> dict:
    """{'rdb': the compiled page exists, 'rdbNewer': it is not older than the .ctg}
    (export_document adds 'rowsMatch' and 'current')."""
    if not path:
        return {"rdb": False}
    folder, base = os.path.split(os.path.abspath(path))
    stem = os.path.splitext(base)[0].lower()
    rdb = None
    try:
        for f in os.listdir(folder):
            if f.lower() == stem + ".rdb":
                rdb = os.path.join(folder, f)
                break
    except OSError:
        return {"rdb": False}
    if rdb is None:
        return {"rdb": False}
    out = {"rdb": True}
    try:
        out["rdbNewer"] = os.path.getmtime(rdb) >= os.path.getmtime(path)
    except OSError:
        pass
    return out


_WIDTHS: Dict[str, Tuple[tuple, Dict[str, int]]] = {}


def field_widths(project_dir: str, table: str) -> Optional[Dict[str, int]]:
    """{FIELD: width} of the project's <table>.DBF header (None without the table); cached
    by the file's size + mtime."""
    from ..io import pgregister as PR

    if not project_dir:
        return None
    path = PR._find_file(project_dir, table + ".DBF")
    if path is None:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = os.path.normcase(os.path.abspath(path))
    stamp = (st.st_size, st.st_mtime_ns)
    got = _WIDTHS.get(key)
    if got is not None and got[0] == stamp:
        return got[1]
    from ..format.dbf import Dbf

    try:
        with Dbf(path, writable=False) as d:
            w = {n.upper(): ln for n, _o, ln in d.fields}
    except OSError, ValueError, struct.error:
        return None
    _WIDTHS[key] = (stamp, w)
    return w


def _cut_to_widths(rows: Dict[str, List[Dict[str, str]]], project_dir: str, notes: Optional[list]) -> None:
    """Cut every value longer than its project field to the width the DBF stores (the
    register's Dbf.encode: bytes of the page code page, at a character boundary); the
    compiler reads the stored form."""
    from ..format.textcodec import truncate

    for table, lst in rows.items():
        w = field_widths(project_dir, table)
        if not w:
            continue
        for r in lst:
            for k, v in list(r.items()):
                n = w.get(k.upper())
                if n is None or not v:
                    continue
                if len(v) <= n and ASCII_SAME and v.isascii():
                    continue  # (an ASCII value: as many bytes as characters)
                try:
                    raw = v.encode(CODEC)
                except UnicodeEncodeError:
                    continue
                if len(raw) > n:
                    r[k] = truncate(raw, n).decode(CODEC)
                    if notes is not None:
                        notes.append(
                            "%s AN %s: %s cut to the %d bytes of %s.DBF"
                            % (table, r.get("AN", ""), k, n, table)
                        )


def page_tables(doc, page_name: str, notes: Optional[list] = None) -> Dict[str, List[Dict[str, str]]]:
    """The compiler's input rows of the page: io/pgregister.page_rows of the in-memory
    records (what the next save registers) + the page keyboard commands (blank AN, the next
    save's) + the activex rows; a value longer than its project field is cut like the DBF
    stores it (trailing blanks / CR LF are kept: the register stores them too)."""
    from ..io import pgregister as PR

    rows = PR.page_rows(doc.parsed, page_name)
    keys = []
    try:
        keys = doc.page_keyboard_commands()
    except Exception:  # noqa: BLE001 -- no project: none
        _note_exc("pagemodel.page_tables")
        keys = []
    for r in keys:
        row = {"PAGE": page_name, "AN": ""}
        row.update({k: v for k, v in r.items() if k.upper() not in ("PAGE", "AN")})
        rows["pgkey"].append(row)
    try:
        rows["activex"] = PR.activex_rows(doc.parsed, page_name)
    except Exception:  # noqa: BLE001
        _note_exc("pagemodel.page_tables")
        rows["activex"] = []
    if doc.path:
        _cut_to_widths(rows, os.path.dirname(os.path.abspath(doc.path)), notes)
    return rows


def _key(row: Dict[str, str]) -> tuple:
    """A row as a comparable value: PAGE without case (Citect page names are not case
    sensitive)."""
    return tuple(sorted((k, v.lower() if k == "PAGE" else v) for k, v in row.items()))


def compare_rows(
    project_dir: str, page: str, tables: Dict[str, List[Dict[str, str]]]
) -> Dict[str, Tuple[int, int, int]]:
    """Compare exported rows with the project's stored rows."""
    from ..format.dbf import Dbf
    from ..io import pgregister as PR

    out: Dict[str, Tuple[int, int, int]] = {}
    for table, rows in tables.items():
        path = PR._find_file(project_dir, table + ".DBF")
        if path is None:
            continue
        with Dbf(path, writable=False) as d:
            if "PAGE" not in d.fmap:
                continue
            ndx = PR._open_ndx(project_dir, table, writable=False)
            try:
                have = [r for _i, r in sorted(PR._page_records(d, ndx, "PAGE", page)) if r[:1] != b"*"]
            finally:
                if ndx is not None:
                    ndx.close()
            if table == "activex":
                ans = {r.get("AN") for r in rows}
                have = [r for r in have if (d.field(r, "AN") if "AN" in d.fmap else None) in ans]
            try:
                enc = d.encode_many(rows)
            except Exception:  # noqa: BLE001 -- a value the code page cannot store
                _note_exc("pagemodel.compare_rows")
                out[table] = (len(have), 0, 0)
                continue
            po, pl = d.fmap["PAGE"]

            def norm(rec: bytes) -> bytes:
                return b" " + rec[1:po] + rec[po : po + pl].lower() + rec[po + pl :]

            # the multisets of the normalised records, compared SORTED (two records differ in
            # their first fields, so the sort reads little of each).
            # With one PAGE spelling on both sides and every flag byte blank (the usual case)
            # a record is its own key: equal after norm exactly when equal
            spellings = {r[po : po + pl] for r in enc}
            spellings.update(r[po : po + pl] for r in have)
            blank = all(r[:1] == b" " for r in have) and all(r[:1] == b" " for r in enc)
            if len(spellings) <= 1 and blank:
                sw, sg, record = sorted(enc), sorted(have), norm
            else:
                sw, sg = sorted(norm(r) for r in enc), sorted(norm(r) for r in have)
                record = lambda k: k  # noqa: E731
            equal = i = j = 0
            rest_w: list = []
            rest_g: list = []
            while i < len(sw) and j < len(sg):
                a, b = sw[i], sg[j]
                if a == b:
                    equal += 1
                    i += 1
                    j += 1
                elif a < b:
                    rest_w.append(a)
                    i += 1
                else:
                    rest_g.append(b)
                    j += 1
            rest_w.extend(sw[i:])
            rest_g.extend(sg[j:])
            if rest_w and rest_g:
                kw: collections.Counter = collections.Counter()
                kg: collections.Counter = collections.Counter()
                for r, c in collections.Counter(rest_w).items():
                    kw[_key(d.decode(record(r)))] += c
                for r, c in collections.Counter(rest_g).items():
                    kg[_key(d.decode(record(r)))] += c
                equal += sum((kw & kg).values())
        out[table] = (len(have), len(enc), equal)
    return out


def export_document(doc, *, owners: bool = True, tables: bool = True) -> dict:
    """The ctsim-page/0 model of an EditableDocument (saved or not)."""
    parsed = doc.parsed
    objs = parsed.page.objects
    par = _parents(objs)
    kids: Dict[int, List[int]] = {}
    for i, p in enumerate(par):
        if p is not None:
            kids.setdefault(p, []).append(i)
    notes: List[str] = []
    resolver = None
    if owners:
        try:
            resolver = doc._resolver()
        except Exception:  # noqa: BLE001
            _note_exc("pagemodel.export_document")
            resolver = None
    own = _OwnerInfo(objs, resolver)
    page_name = _page_name(doc)
    path = os.path.abspath(doc.path) if doc.path else ""
    model: dict = {
        "format": FORMAT,
        "generator": _generator(),
        "project": os.path.basename(os.path.dirname(path)) if path else "",
        "projectDir": os.path.dirname(path) if path else "",
        "page": page_name,
        "path": path,
    }
    dirty = bool(getattr(doc, "dirty", False))
    try:
        # the file rewritten since the document read it (CtDraw, another editor): the
        # in-memory records are not the file any more
        moved = bool(doc.changed_on_disk())
    except Exception:  # noqa: BLE001 -- no stamp: as before
        _note_exc("pagemodel.export_document")
        moved = False
    model["source"] = "unsaved" if dirty or moved or not (path and os.path.exists(path)) else "saved"
    model["compiled"] = _rdb_info(path)
    pg = parsed.page
    model["size"] = [pg.page_width, pg.page_height]
    at = parsed.attributes
    model["background"] = at.bg_color0 if at is not None else None
    try:
        rec = doc.page_record()
    except Exception:  # noqa: BLE001
        _note_exc("pagemodel.export_document")
        rec = None
    model["control"] = dict(rec) if rec else {}
    if not rec:
        notes.append("no pages.DBF row (the page is not in a project folder)")
    try:
        assoc = doc.associations()
    except Exception:  # noqa: BLE001
        _note_exc("pagemodel.export_document")
        assoc = []
    model["associations"] = [
        {"name": n, "default": d, "onError": e, "description": s} for n, d, e, s in assoc
    ]
    model["env"] = [[k, v] for k, v in (at.list1.pairs if at is not None else ()) if not k.startswith("!")]
    if tables:
        model["tables"] = page_tables(doc, page_name, notes)
        c = model["compiled"]
        if c.get("rdb") and model["control"]:
            # 'current': the document is its file, the .RDB is not older
            # than it and the rows a save would register are the project's -- the engine may
            # read the .RDB; else it compiles these rows
            try:
                res = compare_rows(os.path.dirname(path), page_name, model["tables"])
                c["rowsMatch"] = all(s == e == q for s, e, q in res.values())
            except Exception as e:  # noqa: BLE001 -- an unreadable table: not current
                _note_exc("pagemodel.export_document")
                c["rowsMatch"] = False
                notes.append("the project's page rows do not read (%s: %s)" % (type(e).__name__, e))
            c["current"] = bool(model["source"] == "saved" and c.get("rdbNewer") and c["rowsMatch"])
    out = []
    seen: Dict[int, int] = {}
    # (the value bags the page's rows just read: a text's or symbol set's TXTMODE is read
    # from them, not walked again)
    from ..io import pgregister as PR

    value_bags = PR.value_bag_memo(page_name)
    for i, po in enumerate(objs):
        if po.type_code in INPUT_TCS:
            if par[i] is None:
                notes.append("record %d: a command record without an owner" % i)
            continue
        an = _an(po)
        if an <= 0:
            notes.append("record %d: no handle" % i)
            continue
        if an in seen:
            notes.append(
                "record %d: AN %d already used by record %d (the first is exported)" % (i, an, seen[an])
            )
            continue
        seen[an] = i
        try:
            out.append(object_summary(objs, i, par, kids, own, notes, value_bags))
        except Exception as e:  # noqa: BLE001 -- one odd record never stops the export
            _note_exc("pagemodel.export_document")
            notes.append("record %d (AN %d): %s: %s" % (i, an, type(e).__name__, e))
            # the object stays (the forest and the AN list stay whole: a parent's
            # 'children' names it), with what needs no section decode
            p = par[i]
            out.append(
                {
                    "an": an,
                    "type": po.type_code,
                    "class": CLASS_NAMES.get(po.type_code, "0x%04X" % po.type_code),
                    "record": i,
                    "parent": _an(objs[p]) if p is not None else 0,
                    "flags": po.subtype_size,
                    "children": [_an(objs[c]) for c in kids.get(i, ()) if objs[c].type_code not in INPUT_TCS],
                }
            )
    model["objects"] = out
    model["notes"] = notes
    return model


def _generator() -> str:
    try:
        from .. import __version__

        return "ctgedit " + __version__
    except Exception:  # noqa: BLE001
        _note_exc("pagemodel._generator")
        return "ctgedit"


def dumps(model: dict, indent: Optional[int] = None) -> str:
    """The model as JSON text (ASCII: the engine and any console code page read it)."""
    if indent is None:
        return json.dumps(model, ensure_ascii=True, separators=(",", ":"))
    return json.dumps(model, ensure_ascii=True, indent=indent)


def loads(text: str) -> dict:
    m = json.loads(text)
    if m.get("format") != FORMAT:
        raise ValueError("not a %s page model (format %r)" % (FORMAT, m.get("format")))
    return m


# --------------------------------------------------------------------------- the round trip
def check_model(model: dict, doc) -> List[str]:
    """Differences between a re-read model and the document."""
    from ..format import genie_place as GP
    from ..format import legacy
    from ..io import pgregister as PR

    objs = doc.parsed.page.objects
    par = _parents(objs)
    kids: Dict[int, List[int]] = {}
    for i, p in enumerate(par):
        if p is not None:
            kids.setdefault(p, []).append(i)
    bad: List[str] = []
    want_ans = []
    seen = set()
    for i, po in enumerate(objs):
        if po.type_code in INPUT_TCS:
            continue
        an = _an(po)
        if an > 0 and an not in seen:
            seen.add(an)
            want_ans.append((an, i))
    got = model.get("objects") or []
    if [o.get("an") for o in got] != [an for an, _i in want_ans]:
        bad.append("the objects are not the document's ANs in record order")
        return bad
    for o, (an, i) in zip(got, want_ans):
        po = objs[i]
        tag = "AN %d (record %d)" % (an, i)
        if o.get("record") != i or o.get("type") != po.type_code or o.get("flags") != po.subtype_size:
            bad.append(tag + ": record / type / flags")
        try:
            r = GP.decode(po)
        except Exception as e:  # noqa: BLE001
            # a record no decoder reads (a pre-v7 page: Include STANDARD.CTG) is only a
            # difference when the model claims to have read it
            _note_exc("pagemodel.check_model")
            if any(k in o for k in ("rect", "colour", "text", "slots", "metadata")):
                bad.append(tag + ": the strict decode fails (%s)" % e)
            continue
        if (
            o.get("rect") != list(r.rect1)
            or o.get("bounds") != list(r.rect2)
            or o.get("pos") != list(r.anchor)
        ):
            bad.append(tag + ": rect / bounds / pos")
        if (o.get("name") or "") != r.name:
            bad.append(tag + ": name")
        sl = [[m] + [float(v) for v in x] for m, x in r.slots]
        if "slots" in o:
            if o["slots"] != sl:
                bad.append(tag + ": slots")
        elif any(s[0] for s in sl):
            bad.append(tag + ": slots missing")
        p = par[i]
        if o.get("parent") != (_an(objs[p]) if p is not None else 0):
            bad.append(tag + ": parent")
        ch = [c for c in kids.get(i, ()) if objs[c].type_code not in INPUT_TCS]
        if o.get("children") != [_an(objs[c]) for c in ch]:
            bad.append(tag + ": children")
        tcs = {objs[c].type_code for c in kids.get(i, ())}
        if bool(o.get("hasTouch")) != (TOUCH_TC in tcs) or bool(o.get("hasKey")) != (KEY_TC in tcs):
            bad.append(tag + ": hasTouch / hasKey")
        # the colour-state array: re-encode to the stored bytes
        L = CS.layout(po)
        if L is not None:
            c = o.get("colour")
            if c is None or encode_colour(c) != po.body_raw[L["at"] : L["end"]]:
                bad.append(tag + ": colour-state array")
        elif o.get("colour") is not None:
            bad.append(tag + ": a colour for a record that does not walk")
        # metadata: the runtime section = the value bag's METADATA_n (saver rule)
        md = o.get("metadata") or []
        if po.subtype_size & METADATA_FLAG:
            vals = PR._value_bag(po)
            bag = {k: v for k, v in vals}
            want = []
            k = 0
            while bag.get("METADATA_%d" % k):
                nm, _s, v = bag["METADATA_%d" % k].lstrip("=").partition("=")
                want.append([nm, v])
                k += 1
            if vals and md != want:
                bad.append(tag + ": metadata")
        elif md:
            bad.append(tag + ": metadata without the section")
        if po.type_code == TEXT_TC:
            t = o.get("text")
            cls = r.cls
            ml = GP.metadata_len(cls) if r.dw & METADATA_FLAG else 0
            try:
                sl_len = GP.text_strings_len(cls[ml:])
            except struct.error:
                sl_len = None
            if sl_len is None:
                if t is not None:
                    bad.append(tag + ": text section that does not read")
            elif t is None or encode_text(t) != cls[ml : ml + sl_len]:
                bad.append(tag + ": text string section")
        if po.type_code == SET_TC:
            ss = o.get("symbolSet") or {}
            cs = SS.class_section(po)
            states = SS.state_children(objs, i)
            if ss.get("states") != len(states) or (cs is not None and ss.get("declared") != cs["declared"]):
                bad.append(tag + ": symbol set states / declared")
            for n, (k, name) in enumerate(zip(states, ss.get("names") or [])):
                sym = SS.state_symbol(objs[k])
                if sym is not None and name != "%s.%s" % (sym[0], sym[1]):
                    bad.append(tag + ": state %d name" % n)
        if po.type_code in COUNTED_NODE_TCS or po.type_code == LINE_TC:
            if po.subtype_size & 0x2:
                want = G.get_object_nodes(po)
                if want is not None and o.get("vertices") != [list(x) for x in want]:
                    bad.append(tag + ": vertices")
        if po.type_code == TREND_TC:
            ti = legacy.trend_info(po)
            tr = o.get("trend")
            if ti is not None and (
                tr is None or tr["samples"] != ti["samples"] or tr["pens"] != [list(x) for x in ti["pens"]]
            ):
                bad.append(tag + ": trend")
    if "tables" in model:
        page = model.get("page") or ""
        rows = PR.page_rows(doc.parsed, page)
        if doc.path:
            _cut_to_widths(rows, os.path.dirname(os.path.abspath(doc.path)), None)
        for t, lst in rows.items():
            have = [r for r in (model["tables"].get(t) or []) if t != "pgkey" or r.get("AN")]
            if have != lst:
                bad.append("table %s" % t)
    return bad


from ..infra import caches as _caches  # noqa: E402

_caches.register("pagemodel.widths", _WIDTHS, on=("tests",))
