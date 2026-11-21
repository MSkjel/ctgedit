"""Record live page captures for the simulator's page conformance.

python tools/sim_page_capture.py classes [page ...]
python tools/sim_page_capture.py tags <page> [--assoc name=value ...]
python tools/sim_page_capture.py run --out <conformance/pages> [--only page[/case]]
python tools/sim_page_capture.py hold [--minutes 90]
python tools/sim_page_capture.py exec "R <tag>" "C <window> <call>" ...
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import datetime as _dt
import glob
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import Counter, OrderedDict
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FORMAT = "ctsim-pagecase/1"
#: what verify reads: /0 = the first recordings (no alarms, no equipment state)
FORMATS = ("ctsim-pagecase/0", FORMAT)
LOCK = r"C:\ciprobe\runtime.lock"
OWNER = r"C:\ciprobe\runtime.owner"
OWNER_TEXT = "ctgedit page captures v1, ExampleSA"
PLAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim_page_capture_plan.json")
CTSIM_CANDIDATES = (
    os.path.join(
        ROOT, "external", "cicode-sim", "engine", "src", "ctsim", "bin", "Release", "net48", "ctsim.exe"
    ),
)
#: WinNewAt mode: no caption (16, implies no resize / no icons), no dynamic resizing (1024),
#: no scroll bars (16384 + 32768): the client area is the page at its own size
WINDOW_MODE = 16 + 1024 + 16384 + 32768


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# --------------------------------------------------------------------------- project tables
class ProjectModel:
    """The tag / equipment names of a project's include closure (what a page may read)."""

    def __init__(self, project_dir: str):
        from ctgedit.format import projects

        self.dir = project_dir
        self.name = os.path.basename(project_dir.rstrip("\\/"))
        self.dirs = [project_dir] + projects.include_search_dirs(project_dir)
        self.tags: Dict[str, dict] = {}  # lower name -> row (first definition wins)
        self.items: Dict[str, str] = {}  # lower 'Equip.Item' -> tag name
        self.equip_items: Dict[str, List[str]] = {}  # lower equipment -> [Equip.Item]
        self.locvars: Dict[str, dict] = {}
        self.equipment: Dict[str, dict] = {}
        for d in self.dirs:
            for r in self._rows(d, "variable"):
                name = _f(r, "NAME")
                if not name or name.lower() in self.tags:
                    continue
                row = {
                    k: _f(r, k)
                    for k in (
                        "NAME",
                        "TYPE",
                        "UNIT",
                        "ENG_ZERO",
                        "ENG_FULL",
                        "RAW_ZERO",
                        "RAW_FULL",
                        "FORMAT",
                        "EQUIP",
                        "ITEM",
                        "ENG_UNITS",
                        "CLUSTER",
                    )
                }
                row["PROJECT"] = os.path.basename(d.rstrip("\\/"))
                self.tags[name.lower()] = row
                eq, it = row["EQUIP"], row["ITEM"]
                if eq and it:
                    ref = eq + "." + it
                    self.items.setdefault(ref.lower(), name)
                    self.equip_items.setdefault(eq.lower(), []).append(ref)
            for r in self._rows(d, "locvar"):
                name = _f(r, "NAME")
                if name and name.lower() not in self.locvars:
                    self.locvars[name.lower()] = {
                        "NAME": name,
                        "TYPE": _f(r, "TYPE"),
                        "PROJECT": os.path.basename(d.rstrip("\\/")),
                    }
            for r in self._rows(d, "equip"):
                name = _f(r, "NAME")
                if name and name.lower() not in self.equipment:
                    self.equipment[name.lower()] = {
                        k: _f(r, k)
                        for k in (
                            "NAME",
                            "TYPE",
                            "PAGE",
                            "CONTENT",
                            "IODEVICE",
                            "TAGPREFIX",
                            "CLUSTER",
                            "DEFSTATE",
                            "SCHEDULED",
                        )
                    }
        self.alarms: "OrderedDict[str, dict]" = OrderedDict()  # lower tag -> definition
        self._alarm_tables()

    def _alarm_tables(self) -> None:
        """Every configured alarm of the include closure, in the engine's kind order (World/
        AlarmDb.cs Kinds) and table order; the first definition of a tag wins."""
        prio = {}
        for d in self.dirs:
            for r in self._rows(d, "category"):
                c = _f(r, "CATEGORY")
                if c and c not in prio:
                    prio[c] = _f(r, "PRIORITY")
        for kind, table in ALARM_TABLES:
            for d in self.dirs:
                for r in self._rows(d, table):
                    tag = _f(r, "TAG")
                    if not tag or tag.lower() in self.alarms:
                        continue
                    cat = _f(r, "CATEGORY")
                    self.alarms[tag.lower()] = OrderedDict(
                        [
                            ("tag", tag),
                            ("kind", kind),
                            ("equipment", _f(r, "EQUIP")),
                            ("item", _f(r, "ITEM")),
                            ("category", _int_or(cat, 0)),
                            ("priority", _int_or(prio.get(cat, ""), 0)),
                            ("project", os.path.basename(d.rstrip("\\/"))),
                        ]
                    )

    @staticmethod
    def _rows(d: str, table: str) -> List[dict]:
        from ctgedit.format import projects

        for cand in (table + ".DBF", table + ".dbf", table.upper() + ".DBF"):
            p = os.path.join(d, cand)
            if os.path.exists(p):
                return [r for r in projects.read_dbf_rows(p) if not r.get("_deleted")]
        return []

    def resolve(self, ref: str) -> Optional[Tuple[str, str, str]]:
        """(kind, name as the page reads it, variable tag name or '') of a reference text:
        'tag' (a variable tag, optionally with a tag extension), 'item' (Equip.Item),
        'local' (a local variable); None when it names none."""
        low = ref.lower()
        if low in self.tags:
            return "tag", self.tags[low]["NAME"], self.tags[low]["NAME"]
        if low in self.items:
            return "item", ref, self.items[low]
        if low in self.locvars:
            return "local", self.locvars[low]["NAME"], ""
        # a tag extension ('Tag.Q', 'Equip.Item.Field.V'): strip trailing parts
        parts = ref.split(".")
        for n in range(len(parts) - 1, 0, -1):
            head = ".".join(parts[:n])
            tail = [p.lower() for p in parts[n:]]
            if not all(p in _EXTENSIONS for p in tail):
                break
            r = self.resolve(head)
            if r:
                return r
        return None


_EXTENSIONS = {
    "v",
    "q",
    "t",
    "vt",
    "qt",
    "value",
    "field",
    "valid",
    "override",
    "overridemode",
    "controlmode",
    "status",
    "eu",
}

#: (engine kind, alarm table) in the engine's kind order (World/AlarmDb.cs)
ALARM_TABLES = (
    ("digital", "digalm"),
    ("analog", "anaalm"),
    ("advanced", "advalm"),
    ("timestamped", "hresalm"),
    ("ts_digital", "tsdig"),
    ("ts_analog", "tsana"),
    ("multi_digital", "argdig"),
    ("argyle_analog", "argana"),
    ("double_point", "dbptalm"),
)
#: the alarm properties a frame reads per alarm (alarm properties as tags, help 1068485 /
#: 1068486): On, Ack, Disabled (0 / 1), State (the state enumeration + the action masks: 256
#: unacknowledged, 512 disabled ...), OnTime (Unix seconds, UTC) + Millisec, Time (Unix
#: seconds of the last change)
ALARM_PROPS = ("On", "Ack", "Disabled", "State", "OnTime", "Millisec", "Time")
#: the equipment runtime fields (EquipGetProperty / EquipSetProperty, help 1105206 / 1105214)
EQUIP_FIELDS = ("MODE", "STATE", "DRMODE")


def _f(r: dict, k: str) -> str:
    return (r.get(k) or "").strip()


def _int_or(text, default: int) -> int:
    try:
        return int(float(str(text).strip()))
    except TypeError, ValueError:
        return default


# --------------------------------------------------------------------------- page analysis
def ctsim_exe(explicit: Optional[str] = None) -> str:
    for c in ([explicit] if explicit else []) + [os.environ.get("CTSIM", "")] + list(CTSIM_CANDIDATES):
        if c and os.path.isfile(c):
            return c
    raise SystemExit("ctsim.exe not found (build the engine or pass --ctsim)")


def ctsim_dev_exe(ctsim: str) -> str:
    """ctsim-dev.exe (the engine's development commands: pcode ...) next to ``ctsim``, or in the
    engine's ctsim-dev build folder beside ctsim's; else ``ctsim`` itself."""
    here = os.path.dirname(os.path.abspath(ctsim))
    cands = [os.path.join(here, "ctsim-dev.exe")]
    parts = here.split(os.sep)
    if "ctsim" in parts:
        k = len(parts) - 1 - parts[::-1].index("ctsim")
        cands.append(os.sep.join(parts[:k] + ["ctsim-dev"] + parts[k + 1 :] + ["ctsim-dev.exe"]))
    for c in cands:
        if os.path.isfile(c):
            return c
    return ctsim


_HEAD = re.compile(r"^(\S+)\.(\d+)\.(\S+)\s+AN (-?\d+)\s+\((\w+)\)")
_INS = re.compile(r"^\s+[0-9a-f]{4}: [0-9a-f]+\s+(\w+)\s*(.*)$")
_STR = re.compile(r'^(?:STRING) "((?:[^"\\]|\\.)*)"')
_NAME_TOKEN = re.compile(r"[A-Za-z_\x80-\xff][A-Za-z0-9_.\x80-\xff]*")


_UNESCAPE = {"r": "\r", "n": "\n"}


def _unquote(s: str) -> str:
    """The p-code lister's quoting undone (a backslash before a backslash, a quote, r or n)."""
    return re.sub(r"\\(.)", lambda m: _UNESCAPE.get(m.group(1), m.group(1)), s)


def rdb_blocks(rdb: str, ctsim: str) -> List[dict]:
    """The page's compiled blocks: table, record, field, AN, kind, tag loads / stores, string
    constants and called functions (from ``ctsim-dev pcode``)."""
    r = subprocess.run([ctsim_dev_exe(ctsim), "pcode", rdb], capture_output=True, timeout=300)
    text = r.stdout.decode("cp1252", "replace")
    out: List[dict] = []
    cur = None
    for ln in text.splitlines():
        m = _HEAD.match(ln)
        if m:
            cur = {
                "table": m.group(1),
                "record": int(m.group(2)),
                "field": m.group(3),
                "an": int(m.group(4)),
                "kind": m.group(5),
                "loads": [],
                "stores": [],
                "strings": [],
                "calls": [],
            }
            out.append(cur)
            continue
        m = _INS.match(ln)
        if not m or cur is None:
            continue
        op, rest = m.group(1), m.group(2).strip()
        if op in ("Load", "Store"):
            (cur["loads"] if op == "Load" else cur["stores"]).append(rest)
        elif op == "Const":
            s = _STR.match(rest)
            if s:
                cur["strings"].append(_unquote(s.group(1)))
        elif op in ("Func", "FuncCallByLookup", "FuncCallByName"):
            cur["calls"].append(rest.split("(")[0])
    return out


def ctg_strings(ctg: str) -> List[str]:
    """Every property value of the page's records and its page properties."""
    from ctgedit.io import pgregister

    out: List[str] = []
    try:
        objs, _par = pgregister._records(pgregister._parse(ctg))
    except Exception as e:  # noqa: BLE001 -- a page we cannot parse: the RDB alone
        log("could not parse %s: %s" % (ctg, e))
        return out
    for po in objs:
        for b in po.bags or []:
            for k, v in b.pairs:
                if k.startswith("!"):
                    continue
                out.append(v)
                if k.upper().startswith("METADATA_") and "=" in v:
                    out.append(v.split("=", 1)[1])
    return out


def page_tags(
    model: ProjectModel, page: str, ctsim: str, assoc: Optional[Dict[str, str]] = None
) -> "OrderedDict[str, dict]":
    """name as read -> {kind, tag, type, sources: [...]} of every tag the page may read."""
    return page_refs(model, page, ctsim, assoc)[0]


def _substitute(text: str, assoc: Dict[str, str]) -> str:
    """'?name?' associations replaced (``assoc`` keys lower case)."""
    for k, v in assoc.items():
        text = re.sub(r"\?%s\?" % re.escape(k), lambda _m, v=v: v, text, flags=re.I)
    return text


def page_refs(model: ProjectModel, page: str, ctsim: str, assoc: Optional[Dict[str, str]] = None):
    """(tags, equipment) of a page."""
    rdb = os.path.join(model.dir, page + ".RDB")
    ctg = os.path.join(model.dir, page + ".ctg")
    blocks = rdb_blocks(rdb, ctsim) if os.path.exists(rdb) else []
    assoc = {k.lower(): v for k, v in (assoc or {}).items()}
    found: "OrderedDict[str, dict]" = OrderedDict()
    equipment: "OrderedDict[str, dict]" = OrderedDict()

    def add_equipment(name: str, source: str) -> None:
        eq = model.equipment.get((name or "").strip().lower())
        if not eq:
            return
        e = equipment.setdefault(eq["NAME"].lower(), {"name": eq["NAME"], "sources": []})
        if source not in e["sources"]:
            e["sources"].append(source)

    def add(ref: str, source: str) -> bool:
        ref = _substitute(ref, assoc)
        if "?" in ref:
            return False
        r = model.resolve(ref)
        if not r:
            return False
        kind, name, tag = r
        e = found.setdefault(
            name.lower(),
            {
                "name": name,
                "kind": kind,
                "tag": tag,
                "type": (
                    model.tags.get(tag.lower(), {}).get("TYPE")
                    if tag
                    else model.locvars.get(name.lower(), {}).get("TYPE", "")
                )
                or "",
                "sources": [],
            },
        )
        if source not in e["sources"]:
            e["sources"].append(source)
        return True

    def tokens(s: str) -> List[str]:
        return _NAME_TOKEN.findall(s or "")

    for b in blocks:
        for ref in b["loads"] + b["stores"]:
            add(ref, "rdb")
    names: List[str] = []
    for b in blocks:
        names += b["strings"]
    names += ctg_strings(ctg)
    names += list(assoc.values())
    seen = set()
    for s in names:
        for t in tokens(s):
            t = t.strip(".")
            if not t or t.lower() in seen:
                continue
            seen.add(t.lower())
            if add(t, "string"):
                continue
            eq = model.equipment.get(t.lower())
            if eq:
                add_equipment(eq["NAME"], "string")
                for ref in model.equip_items.get(t.lower(), []):
                    add(ref, "equipment " + eq["NAME"])
    for v in assoc.values():
        add_equipment(v, "association")
    want = page.lower()
    for r in model._rows(model.dir, "pgalmind"):
        if _f(r, "PAGE").lower() == want:
            ex = _substitute(_f(r, "EXPR"), assoc)
            if "?" not in ex:
                add_equipment(ex, "alarm indicator")
    for m in found.values():
        if m["kind"] == "item":
            add_equipment(m["name"].rsplit(".", 1)[0], "item")
        if m["tag"]:
            add_equipment(model.tags.get(m["tag"].lower(), {}).get("EQUIP", ""), "item")
    for eq in model.equipment.values():
        if eq.get("SCHEDULED", "").upper() in ("TRUE", "1"):
            add_equipment(eq["NAME"], "scheduled")
    return found, equipment


def page_classes(model: ProjectModel, page: str) -> Dict[str, int]:
    """Animation classes a page exercises, counted from its pg*.DBF rows (the compiler's
    input, one row per animated object or command record)."""
    c: Counter = Counter()
    want = page.lower()
    for r in model._rows(model.dir, "pgdynobj"):
        if _f(r, "PAGE").lower() != want:
            continue
        t, tm, cm = _f(r, "TYPE"), _f(r, "TXTMODE"), _f(r, "COLMODE")
        if _f(r, "MVX") or _f(r, "MVY"):
            c["movement"] += 1
        if _f(r, "ROT"):
            c["rotation"] += 1
        if _f(r, "SZX") or _f(r, "SZY"):
            c["scaling"] += 1
        if _f(r, "MVXSL") or _f(r, "MVYSL") or _f(r, "ROTSL"):
            c["slider"] += 1
        if t not in ("0x0005", "0x000C"):
            if cm in ("", "0") and _f(r, "COL_A"):
                c["fill_colour_onoff"] += 1
            elif cm == "1":
                c["fill_colour_multistate"] += 1
            elif cm == "2":
                c["fill_colour_array"] += 1
            elif cm == "3":
                c["fill_colour_threshold"] += 1
            elif cm == "4":
                c["fill_colour_gradient"] += 1
        elif cm not in ("", "0") or _f(r, "COL_A"):
            c["text_colour"] += 1
        if _f(r, "FIL"):
            c["fill_level"] += 1
        if _f(r, "VISIBLE"):
            c["visibility"] += 1
        if _f(r, "DISABLED"):
            c["disable"] += 1
        if t == "0x0005":
            k = {
                "3": "display_numeric",
                "4": "display_string",
                "2": "display_array",
                "1": "display_multistate",
                "0": "display_onoff",
            }.get(tm)
            if tm == "" and _f(r, "TXT_A"):
                k = "display_onoff"
            if k:
                c[k] += 1
        if t == "0x000C":
            c["symbol_set_" + {"2": "array", "1": "multistate"}.get(tm, "onoff")] += 1
    for table, name in (
        ("pgbutton", "button"),
        ("pgtouch", "touch_command"),
        ("pgkey", "keyboard_command"),
        ("pgadv", "cicode_object"),
        ("pgtrend", "trend"),
        ("pgansym", "animated_symbol"),
        ("pgvertex", "vertex_animation"),
        ("pgalmind", "alarm_indicator"),
        ("pgslider", "slider"),
        ("pgnum", "number_v3"),
        ("pgsym", "symbol_v3"),
        ("pgstr", "string_v3"),
        ("pgtext", "text_v3"),
        ("pgbar", "bar_v3"),
        ("pgcol", "colour_v3"),
    ):
        for r in model._rows(model.dir, table):
            if _f(r, "PAGE").lower() == want and _f(r, "AN"):
                c[name] += 1
    return dict(sorted(c.items()))


# --------------------------------------------------------------------------- CtAPI helper
_HELPER_SRC = r"""
using System;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

class CtCap {
    // CT_TAGVALUE_ITEMS: ctapi.h declares it inside #pragma pack(push, 1), so 38 bytes, no padding
    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    struct Items {
        public uint dwLength; public ulong nTimestamp; public ulong nValueTimestamp; public ulong nQualityTimestamp;
        public byte bGeneral; public byte bSubstatus; public byte bLimit; public byte bExtended;
        public uint nError; public byte bOverride; public byte bControlMode;
    }
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr ctOpen(string a, string b, string c, uint mode);
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern uint ctCicode(IntPtr h, string cmd, uint hWin, uint mode, StringBuilder r, uint len, IntPtr ov);
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern bool ctTagWrite(IntPtr h, string tag, string value);
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern bool ctTagReadEx(IntPtr h, string tag, StringBuilder v, uint len, ref Items it);
    [DllImport("CtApi.dll", SetLastError = true)]
    static extern bool ctClose(IntPtr h);
    [DllImport("CtApi.dll", SetLastError = true)]
    static extern IntPtr ctListNew(IntPtr h, uint mode);
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr ctListAddEx(IntPtr list, string tag, bool raw, int pollMs, double deadband);
    [DllImport("CtApi.dll", SetLastError = true)]
    static extern bool ctListRead(IntPtr list, IntPtr ov);
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern bool ctListData(IntPtr tag, StringBuilder buf, uint len, uint mode);
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern bool ctListItem(IntPtr tag, uint item, StringBuilder buf, uint len, uint mode);
    [DllImport("CtApi.dll", SetLastError = true)]
    static extern bool ctListFree(IntPtr list);

    // a poll list: one ctListRead gives every tag of a page at one moment (ctTagReadEx one
    // by one takes about 12 ms a tag: 11 s for the 895 tags of Overview_L2)
    static readonly System.Collections.Generic.Dictionary<string, IntPtr> Lists =
        new System.Collections.Generic.Dictionary<string, IntPtr>();
    static readonly System.Collections.Generic.Dictionary<string, System.Collections.Generic.List<IntPtr>> ListTags =
        new System.Collections.Generic.Dictionary<string, System.Collections.Generic.List<IntPtr>>();
    // CT_LIST_* items (ctapi.h) and their answer keys, the keys of an "R" answer
    static readonly uint[] ItemIds = { 5, 6, 7, 8, 9, 10, 11, 2, 3, 4 };
    static readonly string[] ItemKeys = { "g", "s", "l", "e", "err", "ovr", "ctl", "t", "vt", "qt" };

    static string ListEntry(IntPtr tag) {
        var sb = new StringBuilder(8192);
        if (!ctListData(tag, sb, 8192, 0))
            return "{\"ok\":false,\"error\":" + Marshal.GetLastWin32Error() + "}";
        var o = new StringBuilder("{\"ok\":true,\"value\":" + J(sb.ToString()));
        for (int i = 0; i < ItemIds.Length; i++) {
            var ib = new StringBuilder(256);
            o.Append(",\"" + ItemKeys[i] + "\":");
            o.Append(ctListItem(tag, ItemIds[i], ib, 256, 0) ? J(ib.ToString()) : "null");
        }
        return o.Append('}').ToString();
    }

    static string J(string s) {
        var sb = new StringBuilder("\"");
        foreach (char c in s) {
            if (c == '"') sb.Append("\\\""); else if (c == '\\') sb.Append("\\\\");
            else if (c < ' ') sb.AppendFormat("\\u{0:x4}", (int)c); else sb.Append(c);
        }
        return sb.Append('"').ToString();
    }

    static int Main() {
        // numbers in the answers and the window argument are culture-free (a Norwegian or
        // German session would otherwise be free to format them its own way)
        Thread.CurrentThread.CurrentCulture = CultureInfo.InvariantCulture;
        var enc = new UTF8Encoding(false);
        var stdin = new StreamReader(Console.OpenStandardInput(), enc);
        var stdout = new StreamWriter(Console.OpenStandardOutput(), enc);
        stdout.AutoFlush = true;
        IntPtr h = ctOpen(null, null, null, 0);
        if (h == IntPtr.Zero) { stdout.WriteLine("{\"ok\":false,\"open\":true,\"error\":" + Marshal.GetLastWin32Error() + "}"); return 2; }
        stdout.WriteLine("{\"ok\":true,\"ready\":true}");
        string line;
        while ((line = stdin.ReadLine()) != null) {
            string[] a = line.Split('\t');
            try {
                if (a[0] == "C") {
                    var sb = new StringBuilder(8192);
                    uint ok = ctCicode(h, a[2], uint.Parse(a[1], CultureInfo.InvariantCulture), 0, sb, 8192, IntPtr.Zero);
                    stdout.WriteLine(ok != 0 ? "{\"ok\":true,\"value\":" + J(sb.ToString()) + "}"
                                             : "{\"ok\":false,\"error\":" + Marshal.GetLastWin32Error() + "}");
                } else if (a[0] == "R") {
                    var sb = new StringBuilder(8192);
                    var it = new Items(); it.dwLength = (uint)Marshal.SizeOf(typeof(Items));
                    bool ok = ctTagReadEx(h, a[1], sb, 8192, ref it);
                    if (!ok) { stdout.WriteLine("{\"ok\":false,\"error\":" + Marshal.GetLastWin32Error() + "}"); continue; }
                    stdout.WriteLine("{\"ok\":true,\"value\":" + J(sb.ToString()) + ",\"g\":" + it.bGeneral + ",\"s\":" + it.bSubstatus
                        + ",\"l\":" + it.bLimit + ",\"e\":" + it.bExtended + ",\"err\":" + it.nError + ",\"ovr\":" + it.bOverride
                        + ",\"ctl\":" + it.bControlMode + ",\"t\":" + it.nTimestamp + ",\"vt\":" + it.nValueTimestamp
                        + ",\"qt\":" + it.nQualityTimestamp + "}");
                } else if (a[0] == "W") {
                    bool ok = ctTagWrite(h, a[1], a[2]);
                    stdout.WriteLine(ok ? "{\"ok\":true}" : "{\"ok\":false,\"error\":" + Marshal.GetLastWin32Error() + "}");
                } else if (a[0] == "LN") {
                    IntPtr l = ctListNew(h, 0);
                    if (l == IntPtr.Zero) { stdout.WriteLine("{\"ok\":false,\"error\":" + Marshal.GetLastWin32Error() + "}"); continue; }
                    Lists[a[1]] = l; ListTags[a[1]] = new System.Collections.Generic.List<IntPtr>();
                    stdout.WriteLine("{\"ok\":true}");
                } else if (a[0] == "LA") {
                    // poll period = the page scan (250 ms), the tag's own deadband
                    IntPtr t = ctListAddEx(Lists[a[1]], a[2], false, int.Parse(a[3], CultureInfo.InvariantCulture), -1.0);
                    if (t == IntPtr.Zero) { stdout.WriteLine("{\"ok\":false,\"error\":" + Marshal.GetLastWin32Error() + "}"); continue; }
                    ListTags[a[1]].Add(t);
                    stdout.WriteLine("{\"ok\":true,\"index\":" + (ListTags[a[1]].Count - 1) + "}");
                } else if (a[0] == "LR") {
                    if (!ctListRead(Lists[a[1]], IntPtr.Zero)) { stdout.WriteLine("{\"ok\":false,\"error\":" + Marshal.GetLastWin32Error() + "}"); continue; }
                    var o = new StringBuilder("{\"ok\":true,\"items\":[");
                    var tags = ListTags[a[1]];
                    for (int i = 0; i < tags.Count; i++) { if (i > 0) o.Append(','); o.Append(ListEntry(tags[i])); }
                    stdout.WriteLine(o.Append("]}").ToString());
                } else if (a[0] == "LF") {
                    if (Lists.ContainsKey(a[1])) { ctListFree(Lists[a[1]]); Lists.Remove(a[1]); ListTags.Remove(a[1]); }
                    stdout.WriteLine("{\"ok\":true}");
                } else if (a[0] == "Q") {
                    break;
                } else {
                    stdout.WriteLine("{\"ok\":false,\"error\":-1,\"bad\":" + J(a[0]) + "}");
                }
            } catch (Exception e) {
                stdout.WriteLine("{\"ok\":false,\"error\":-2,\"exception\":" + J(e.Message) + "}");
            }
        }
        foreach (var l in Lists.Values) ctListFree(l);
        ctClose(h);
        return 0;
    }
}
"""


def helper_exe() -> str:
    """ctcap.exe, built next to ctgedit's private copy of ``Bin (x64)`` (so CtApi.dll's
    managed dependencies bind; nothing is written into the install)."""
    from ctgedit.backend import ctapi

    base_helper = ctapi.helper_path()
    if base_helper is None:
        raise SystemExit("CtAPI is not available on this machine")
    base = os.path.dirname(base_helper)
    tag = hashlib.md5(_HELPER_SRC.encode()).hexdigest()[:8]
    exe = os.path.join(base, "ctcap-%s.exe" % tag)
    if os.path.exists(exe):
        return exe
    cs = os.path.join(base, "ctcap-%s.cs" % tag)
    with open(cs, "w", encoding="ascii") as fh:
        fh.write(_HELPER_SRC)
    r = subprocess.run(
        [ctapi.CSC, "/nologo", "/platform:x64", "/out:" + exe, cs], cwd=base, capture_output=True, text=True
    )
    if r.returncode != 0 or not os.path.exists(exe):
        raise SystemExit("could not build the CtAPI helper:\n" + r.stdout + r.stderr)
    # other builds of the helper (another source) go; one still running stays
    for old in glob.glob(os.path.join(base, "ctcap-*.*")):
        if os.path.basename(old) != os.path.basename(exe):
            try:
                os.remove(old)
            except OSError:
                pass
    return exe


def cicode_str(s: str) -> str:
    """A Cicode string literal: the caret escapes a quote and itself."""
    return '"%s"' % str(s).replace("^", "^^").replace('"', '^"')


class CtApiError(RuntimeError):
    pass


class CtCap:
    """A persistent CtAPI session (one helper process, one JSON line per call)."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.exe = helper_exe()
        self.calls = 0
        self._start()

    def _start(self) -> None:
        self.p = subprocess.Popen(
            [self.exe],
            cwd=os.path.dirname(self.exe),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        threading.Thread(target=self._pump, args=(self.p.stdout, self.q), daemon=True).start()
        first = self._line(60.0)
        if not first.get("ok"):
            self.close()
            raise CtApiError("CtAPI did not open: %s" % first)

    @staticmethod
    def _pump(out, q) -> None:
        """Lines of one helper process into its queue (None at its end)."""
        try:
            for ln in out:
                q.put(ln)
        except OSError, ValueError:
            pass
        finally:
            q.put(None)
            try:
                out.close()
            except Exception:  # noqa: BLE001
                pass

    def _line(self, timeout: float) -> dict:
        try:
            ln = self.q.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise CtApiError("CtAPI did not answer within %.0f s" % timeout)
        if ln is None:
            raise CtApiError("the CtAPI helper ended")
        try:
            return json.loads(ln.decode("utf-8"))
        except ValueError:
            # not an answer (a CLR message on stdout): the session cannot be trusted
            self.close()
            raise CtApiError("the CtAPI helper wrote %r" % ln[:200])

    def alive(self) -> bool:
        return self.p.poll() is None

    def restart(self) -> None:
        """A fresh helper process (after a timeout killed the old one)."""
        self.close()
        self._start()

    def _call(self, *parts: str) -> dict:
        if any("\t" in p or "\n" in p or "\r" in p for p in parts):
            raise ValueError("a CtAPI argument holds a tab or a line break: %r" % (parts,))
        try:
            self.p.stdin.write(("\t".join(parts) + "\n").encode("utf-8"))
            self.p.stdin.flush()
        except (OSError, ValueError) as e:
            raise CtApiError("the CtAPI helper is gone (%s)" % e)
        self.calls += 1
        return self._line(self.timeout)

    def cicode(self, stmt: str, window: int = 0) -> dict:
        """One function call with literal arguments, as a task of display window ``window``."""
        return self._call("C", str(int(window)), stmt)

    def read(self, tag: str) -> dict:
        return self._call("R", tag)

    def write(self, tag: str, value) -> dict:
        return self._call("W", tag, _value_text(value))

    def list_new(self, name: str, tags: List[str], poll_ms: int = 250) -> List[dict]:
        """A CtAPI poll list of ``tags`` (subscribed at ``poll_ms``); the answer per tag."""
        r = self._call("LN", name)
        if not r.get("ok"):
            raise CtApiError("ctListNew failed: %s" % r)
        return [self._call("LA", name, t, str(int(poll_ms))) for t in tags]

    def list_read(self, name: str) -> List[dict]:
        """One ctListRead: every tag of the list at one moment, each answer shaped like a
        ``read`` answer (numbers parsed from ctListItem's strings)."""
        r = self._call("LR", name)
        if not r.get("ok"):
            raise CtApiError("ctListRead failed: %s" % r)
        return [_list_item(it) for it in r["items"]]

    def list_free(self, name: str) -> None:
        self._call("LF", name)

    def close(self) -> None:
        try:
            if self.p.poll() is None:
                try:
                    self.p.stdin.write(b"Q\n")
                    self.p.stdin.flush()
                    self.p.wait(5)
                except Exception:  # noqa: BLE001
                    pass
            if self.p.poll() is None:
                self.p.kill()
                self.p.wait(5)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.p.stdin.close()  # stdout: the pump thread closes it at its end
        except Exception:  # noqa: BLE001
            pass


def _list_item(it: dict) -> dict:
    """A ctListData + ctListItem answer (strings) as a ctTagReadEx answer (numbers)."""
    if not it.get("ok"):
        return it
    out = {"ok": True, "value": it.get("value", "")}
    for k in ("g", "s", "l", "e", "err", "ovr", "ctl", "t", "vt", "qt"):
        v = it.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s.upper() in ("TRUE", "FALSE"):
            out[k] = 1 if s.upper() == "TRUE" else 0
            continue
        try:
            out[k] = int(s)
        except ValueError:
            try:
                out[k] = int(float(s))
            except ValueError:
                out[k + "Text"] = s
    return out


def _value_text(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def quality_word(r: dict) -> int:
    """The 64-bit QUALITY (engine World/Quality.cs layout) of a ctTagReadEx answer."""
    g = int(r.get("g", 3))
    if g > 3:  # an OPC quality byte
        q = g & 0xFF
    else:
        q = (g & 3) << 6 | (int(r.get("s", 0)) & 0xF) << 2 | (int(r.get("l", 0)) & 3)
    q |= (int(r.get("e", 0)) & 0xFF) << 8
    if r.get("ovr"):
        q |= 1 << 16
    if r.get("ctl"):
        q |= 1 << 17
    q |= (int(r.get("err", 0)) & 0xFFFFFFFF) << 32
    return q


_QNAMES = {
    0xC0: "good",
    0x00: "bad",
    0x40: "uncertain",
    0x18: "bad_comm_failure",
    0x08: "bad_not_connected",
    0x04: "bad_config",
    0x1C: "bad_out_of_service",
    0x20: "bad_waiting_for_initial_data",
    0xD8: "good_local_override",
}


def quality_name(q: int) -> str:
    """A scenario quality: a name when the word is exactly one, else '0x..'."""
    return _QNAMES.get(q, "0x%x" % q)


def filetime_iso(ft: int) -> Optional[str]:
    if not ft:
        return None
    try:
        t = _dt.datetime(1601, 1, 1) + _dt.timedelta(microseconds=ft // 10)
        return t.isoformat(timespec="milliseconds") + "Z"
    except OverflowError, ValueError:
        return None


_NUMERIC = {"DIGITAL", "INT", "UINT", "LONG", "ULONG", "REAL", "LONGREAL", "BCD", "LONGBCD", "BYTE", "INT16"}


def snapshot_entry(meta: dict, r: dict) -> dict:
    if not r.get("ok"):
        return {
            "error": int(r.get("error", 0)),
            "code": int(r.get("error", 0)) - 0x10000000 if int(r.get("error", 0)) > 0x10000000 else None,
        }
    text = r["value"]
    val = text
    if (meta.get("type") or "").upper() in _NUMERIC:
        try:
            f = float(text)
            val = int(f) if f.is_integer() and "." not in text and "e" not in text.lower() else f
        except ValueError:
            pass
    q = quality_word(r)
    e = {"value": val, "text": text, "quality": quality_name(q)}
    if q & 0xFFFF00 or q >> 32:
        e["qualityWord"] = "0x%x" % q
    ts = filetime_iso(int(r.get("vt") or r.get("t") or 0))
    if ts:
        e["timestamp"] = ts
    return e


# --------------------------------------------------------------------------- windows
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
for _fn, _res, _args in (
    ("GetWindowRect", wt.BOOL, [wt.HWND, ctypes.POINTER(wt.RECT)]),
    ("GetClientRect", wt.BOOL, [wt.HWND, ctypes.POINTER(wt.RECT)]),
    ("ClientToScreen", wt.BOOL, [wt.HWND, ctypes.POINTER(wt.POINT)]),
    ("IsWindow", wt.BOOL, [wt.HWND]),
    ("IsWindowVisible", wt.BOOL, [wt.HWND]),
    ("GetDC", wt.HDC, [wt.HWND]),
    ("ReleaseDC", ctypes.c_int, [wt.HWND, wt.HDC]),
    ("PrintWindow", wt.BOOL, [wt.HWND, wt.HDC, wt.UINT]),
    ("GetCursorPos", wt.BOOL, [ctypes.POINTER(wt.POINT)]),
    ("SetCursorPos", wt.BOOL, [ctypes.c_int, ctypes.c_int]),
    ("GetWindowTextW", ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
    ("GetClassNameW", ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
    ("GetWindowLongW", ctypes.c_long, [wt.HWND, ctypes.c_int]),
    ("GetDpiForWindow", wt.UINT, [wt.HWND]),
    ("SystemParametersInfoW", wt.BOOL, [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]),
):
    getattr(_user32, _fn).restype = _res
    getattr(_user32, _fn).argtypes = _args
for _fn, _res, _args in (
    ("CreateCompatibleDC", wt.HDC, [wt.HDC]),
    ("CreateCompatibleBitmap", wt.HBITMAP, [wt.HDC, ctypes.c_int, ctypes.c_int]),
    ("SelectObject", wt.HGDIOBJ, [wt.HDC, wt.HGDIOBJ]),
    ("DeleteObject", wt.BOOL, [wt.HGDIOBJ]),
    ("DeleteDC", wt.BOOL, [wt.HDC]),
    (
        "GetDIBits",
        ctypes.c_int,
        [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT],
    ),
):
    getattr(_gdi32, _fn).restype = _res
    getattr(_gdi32, _fn).argtypes = _args

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 1, 2


def dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            _user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def window_info(hwnd: int) -> dict:
    wr, cr, pt = wt.RECT(), wt.RECT(), wt.POINT(0, 0)
    _user32.GetWindowRect(hwnd, ctypes.byref(wr))
    _user32.GetClientRect(hwnd, ctypes.byref(cr))
    _user32.ClientToScreen(hwnd, ctypes.byref(pt))
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
    cls = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, cls, 256)
    try:
        dpi = int(_user32.GetDpiForWindow(hwnd))
    except Exception:  # noqa: BLE001
        dpi = 96
    return {
        "hwnd": "0x%x" % hwnd,
        "class": cls.value,
        "caption": buf.value,
        "rect": [wr.left, wr.top, wr.right, wr.bottom],
        "client": [pt.x, pt.y, cr.right - cr.left, cr.bottom - cr.top],
        "style": "0x%08x" % (_user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF),
        "dpi": dpi,
    }


class _BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


def capture_client(hwnd: int):
    """The window's client area as a PIL RGB image (PrintWindow, PW_CLIENTONLY |
    PW_RENDERFULLCONTENT: rendered by the window even when covered)."""
    from PIL import Image

    cr = wt.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(cr))
    w, h = cr.right - cr.left, cr.bottom - cr.top
    if w <= 0 or h <= 0:
        raise RuntimeError("the window has no client area")
    sdc = _user32.GetDC(hwnd)
    if not sdc:
        raise RuntimeError("GetDC failed (%d)" % ctypes.get_last_error())
    mdc = bmp = old = None
    try:
        mdc = _gdi32.CreateCompatibleDC(sdc)
        bmp = _gdi32.CreateCompatibleBitmap(sdc, w, h)
        if not mdc or not bmp:
            raise RuntimeError("no memory bitmap of %dx%d" % (w, h))
        old = _gdi32.SelectObject(mdc, bmp)
        if not _user32.PrintWindow(hwnd, mdc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            raise RuntimeError("PrintWindow failed (%d)" % ctypes.get_last_error())
        # GetDIBits wants the bitmap out of every device context
        _gdi32.SelectObject(mdc, old)
        old = None
        bi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if _gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0) != h:
            raise RuntimeError("GetDIBits failed")
        img = Image.frombuffer("RGBX", (w, h), buf.raw, "raw", "BGRX", 0, 1).convert("RGB")
    finally:
        if old:
            _gdi32.SelectObject(mdc, old)
        if bmp:
            _gdi32.DeleteObject(bmp)
        if mdc:
            _gdi32.DeleteDC(mdc)
        _user32.ReleaseDC(hwnd, sdc)
    return img


def save_png(img, path: str) -> int:
    img.save(path, "PNG", optimize=True)
    return os.path.getsize(path)


# --------------------------------------------------------------------------- lock + runtime
class RuntimeLock:
    """C:\\ciprobe\\runtime.lock, msvcrt.locking on byte 0, held for the whole batch; the
    owner file names the holder while it is held."""

    def __init__(self, journal: List[str]):
        self.journal = journal
        self.fh = None

    def __enter__(self):
        import msvcrt

        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        self.fh = open(LOCK, "a+")
        self.fh.seek(0)
        t0 = time.time()
        waited = False
        while True:
            try:
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if not waited:
                    owner = ""
                    try:
                        owner = open(OWNER, encoding="utf-8", errors="replace").read().strip()
                    except OSError:
                        pass
                    log("runtime.lock is held (%s); waiting" % (owner or "no owner file"))
                    self.journal.append("%s lock busy (%s), waiting" % (_now(), owner or "?"))
                    waited = True
                time.sleep(5)
        with open(OWNER, "w", encoding="utf-8") as fh:
            fh.write("%s, %s\n" % (OWNER_TEXT, time.strftime("%Y-%m-%d %H:%M:%S")))
        self.journal.append("%s lock taken (waited %.0f s)" % (_now(), time.time() - t0))
        log("runtime.lock taken")
        return self

    def __exit__(self, *exc):
        import msvcrt

        try:
            os.remove(OWNER)
        except OSError:
            pass
        try:
            self.fh.seek(0)
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.fh.close()
        self.journal.append("%s lock released" % _now())
        log("runtime.lock released")
        return False


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class DialogWatch:
    """Answers ONLY the demo-mode 'protection key' box of the runtime; any other message
    box is reported (``foreign``) and left alone."""

    def __init__(self):
        from ctgedit.backend import runtime, winui

        self.runtime, self.winui = runtime, winui
        self.stop_ev = threading.Event()
        self.foreign: List[str] = []
        self.answered: List[str] = []
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self) -> None:
        warned = set()
        while not self.stop_ev.is_set():
            try:
                for im in self.runtime.IMAGES:
                    for pid in self.winui.pids_of(im):
                        for h in self.winui.windows_of(pid):
                            kids = self.winui.children(h)
                            if not self.winui.is_message_box(kids):
                                continue
                            msg = self.winui.message_text(kids) or ""
                            if "protection key" in msg.lower():
                                r = self.winui.dismiss_message_box(h, kids, prefer="OK")
                                self.answered.append("%s %r -> %s" % (_now(), msg[:80], r))
                                log("answered the demo-mode box:", r)
                            elif h not in warned:
                                warned.add(h)
                                self.foreign.append("%s %s: %r" % (_now(), im, msg[:200]))
                                log("NOT answering a runtime dialog:", repr(msg[:200]))
            except Exception as e:  # noqa: BLE001 -- keep watching
                log("dialog watch:", e)
            self.stop_ev.wait(0.5)

    def stop(self) -> None:
        self.stop_ev.set()
        self.t.join(3)


def runtime_version() -> dict:
    from ctgedit.backend.compiler import scada_bin

    out = {"product": "AVEVA Plant SCADA"}
    try:
        import win32api

        exe = os.path.join(scada_bin(), "Citect32.exe")
        info = win32api.GetFileVersionInfo(exe, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        out["citect32"] = "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        try:
            lang, cp = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")[0]
            key = "\\StringFileInfo\\%04x%04x\\ProductVersion" % (lang, cp)
            out["productVersion"] = win32api.GetFileVersionInfo(exe, key).strip()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    return out


# --------------------------------------------------------------------------- the session
def state_dir() -> str:
    d = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ctgedit", "simcap")
    os.makedirs(d, exist_ok=True)
    return d


def _journal_path() -> str:
    return os.path.join(state_dir(), "journal.log")


def _pid_alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    code = wt.DWORD()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def active_hold() -> Optional[dict]:
    """The running ``hold`` process's state, when one holds the lock and the runtime."""
    p = os.path.join(state_dir(), "hold.json")
    try:
        with open(p, encoding="utf-8") as fh:
            st = json.load(fh)
    except OSError, ValueError:
        return None
    # the pid alone could be a reused one of a hold that died: the lock must be held too
    if st.get("ready") and _pid_alive(int(st.get("pid", 0))) and _lock_held():
        return st
    return None


# --------------------------------------------------------------------------- shared state
#: citect.ini sections the runtime itself rewrites when it shuts down (window positions of
#: its kernel / server windows, in the order its processes exit)
def _positions_section(name: str) -> bool:
    return name.endswith("windowpositions")


def _ini_layout(raw: bytes) -> List[Tuple[str, List[str]]]:
    """(lower section name, its non-blank lines) in file order; '' = before any section."""
    out: List[Tuple[str, List[str]]] = [("", [])]
    for ln in raw.decode("latin-1").splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            out.append((s[1:-1].strip().lower(), []))
        elif s:
            out[-1][1].append(s)
    return out


def ini_differences(before: bytes, now: bytes, ignore_run: bool = True) -> List[str]:
    """The citect.ini sections whose content differs, leaving out what a page-capture batch
    changes on purpose ([CtEdit] Run) and the window positions the runtime writes at its
    shutdown. Empty = only those changed."""

    def key(raw: bytes):
        out = []
        for name, lines in _ini_layout(raw):
            if _positions_section(name):
                continue
            if ignore_run and name == "ctedit":
                lines = [ln for ln in lines if ln.split("=", 1)[0].strip().lower() != "run"]
            out.append((name, lines))
        return out

    a, b = key(before), key(now)
    if a == b:
        return []
    da, db = dict(a), dict(b)
    names = [n for n, _ in a] + [n for n, _ in b if n not in da]
    return [n or "(top)" for n in OrderedDict.fromkeys(names) if da.get(n) != db.get(n)] or [
        "(section order)"
    ]


def runtime_data_dir() -> Optional[str]:
    """[CtEdit] Data of the active citect.ini: where the IO server keeps its caches."""
    from ctgedit.io.citect_config import ini_value

    d = ini_value("CtEdit", "Data", "").strip()
    return d if d and os.path.isdir(d) else None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def override_inventory(data_dir: str) -> Dict[str, Dict[str, Optional[str]]]:
    """{cache file: {tag: its OverrideMode value or None}} of every tag whose Override
    element the IO server's persistence caches (``<Data>\\*.cache``, XML) hold. A tag that
    was never overridden has no Override element; TagSetOverride* creates one, and the IO
    server keeps it across restarts."""
    import xml.etree.ElementTree as ET

    out: Dict[str, Dict[str, Optional[str]]] = {}
    for p in sorted(glob.glob(os.path.join(data_dir, "*.cache"))):
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError, OSError:
            continue
        found: Dict[str, Optional[str]] = {}
        for tag in root.iter():
            if _local(tag.tag) != "tag":
                continue
            elements = {}
            for el in tag.iter():
                if _local(el.tag) == "element":
                    item = next((i for i in el.iter() if _local(i.tag) == "item"), None)
                    elements[el.get("name")] = item.text if item is not None else None
            if "Override" in elements:
                found[tag.get("name")] = elements.get("OverrideMode")
        if found:
            out[os.path.basename(p)] = found
    return out


def new_overrides(
    before: Dict[str, Dict[str, Optional[str]]], after: Dict[str, Dict[str, Optional[str]]]
) -> Dict[str, Dict[str, Optional[str]]]:
    out = {}
    for f, tags in after.items():
        n = {t: m for t, m in tags.items() if t not in before.get(f, {})}
        if n:
            out[f] = n
    return out


_OVERRIDE_ELEMENT = re.compile(r'[ \t]*<element name="Override">.*?</element>[ \t]*(?:\r\n|\n)?', re.S)


def remove_override_elements(path: str, tags: List[str], backup_dir: str) -> List[str]:
    """Remove the Override element of ``tags`` from one persistence cache, every other byte
    kept; the file is checked (same tags, same elements but those) before it replaces the
    original, which is copied to ``backup_dir`` first. Returns the tags whose element went."""
    import shutil
    import xml.etree.ElementTree as ET
    from xml.sax.saxutils import quoteattr
    from ctgedit.format.fileio import write_atomic

    raw = open(path, "rb").read()
    text = raw.decode("utf-8")
    removed = []
    for t in tags:
        m = re.search(r"<tag name=%s>" % re.escape(quoteattr(t)), text)
        if not m:
            continue
        end = text.find("</tag>", m.end())
        if end < 0:
            continue
        block = text[m.end() : end]
        em = _OVERRIDE_ELEMENT.search(block)
        if not em:
            continue
        text = text[: m.end()] + block[: em.start()] + block[em.end() :] + text[end:]
        removed.append(t)
    if not removed:
        return []
    new = text.encode("utf-8")

    def shape(data: bytes):
        """[(tag, [(element name, element XML)])] of a cache (an element's tail, the
        whitespace before the next one, left out: removing a sibling shifts it)."""
        out = []
        for tag in ET.fromstring(data).iter():
            if _local(tag.tag) != "tag":
                continue
            els = []
            for group in tag:
                if _local(group.tag) == "elements":
                    for el in group:
                        el.tail = None
                        els.append((el.get("name"), ET.tostring(el)))
            out.append((tag.get("name"), els))
        return out

    gone = set(removed)
    expect = [(n, [e for e in els if not (n in gone and e[0] == "Override")]) for n, els in shape(raw)]
    if shape(new) != expect:
        raise RuntimeError("%s: the edited cache does not check out; left unchanged" % path)
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copyfile(path, os.path.join(backup_dir, os.path.basename(path)))
    write_atomic(path, new)
    return removed


class RuntimeSession:
    """The batch's runtime session."""

    def __init__(
        self, project_dir: str, journal: List[str], settle: float = 20.0, keep_overrides: bool = False
    ):
        self.project_dir = project_dir
        self.journal = journal
        self.settle = settle
        self.keep_overrides = keep_overrides
        self.lock = RuntimeLock(journal)
        self.watch: Optional[DialogWatch] = None
        self.cap: Optional[CtCap] = None
        self.run_before: Optional[str] = None
        self.run_changed = False
        self.started = False
        self.ini_path: Optional[str] = None
        self.ini_before: Optional[bytes] = None
        self.data_dir: Optional[str] = None
        self.overrides_before: Optional[Dict[str, Dict[str, Optional[str]]]] = None

    def note(self, text: str) -> None:
        self.journal.append("%s %s" % (_now(), text))
        log(text)

    def __enter__(self):
        from ctgedit.backend import runtime
        from ctgedit.io.citect_config import active_ini_path, ini_value

        self.lock.__enter__()
        try:
            self.ini_path = active_ini_path()
            if self.ini_path:
                with open(self.ini_path, "rb") as fh:
                    self.ini_before = fh.read()
            self.data_dir = runtime_data_dir()
            if self.data_dir:
                self.overrides_before = override_inventory(self.data_dir)
                n = sum(len(v) for v in self.overrides_before.values())
                self.note(
                    "IO server caches in %s: %d tag(s) with an Override element before the "
                    "batch" % (self.data_dir, n)
                )
            self.run_before = ini_value("CtEdit", "Run", "")
            if not runtime.runs_project(self.project_dir):
                path = runtime.set_run_project(self.project_dir)
                self.run_changed = True
                self.note("[CtEdit] Run was %r, set to %s" % (self.run_before, path))
            else:
                self.note("[CtEdit] Run is %s" % self.run_before)
            self.watch = DialogWatch()
            if runtime.is_running() and not self.run_changed:
                self.note("a runtime is already running (%s); using it" % runtime.running_processes())
            else:
                # a runtime started while [CtEdit] Run named another project runs that one
                notes = runtime.start(self.project_dir, restart=True)
                self.started = True
                self.note("runtime started: %s" % "; ".join(notes))
            self._connect()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def _connect(self, timeout: float = 360.0) -> None:
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            self.check_dialogs()
            try:
                self.cap = CtCap()
                r = self.cap.cicode("PageInfo(0)", 0)
                if r.get("ok") and r.get("value", "").strip():
                    self.note(
                        "CtAPI up after %.0f s, window 0 shows %r" % (time.time() - t0, r["value"].strip())
                    )
                    break
                last = r
                self.cap.close()
                self.cap = None
            except CtApiError as e:
                last = str(e)
                self.cap = None
            time.sleep(5)
        else:
            raise RuntimeError("the runtime did not answer CtAPI in %.0f s (%s)" % (timeout, last))
        end = time.time() + self.settle
        while time.time() < end:
            self.check_dialogs()
            time.sleep(1)

    def check_dialogs(self) -> None:
        if self.watch and self.watch.foreign:
            raise RuntimeError("a runtime dialog needs a person: %s" % self.watch.foreign[-1])

    def alive(self) -> bool:
        from ctgedit.backend import runtime

        return runtime.is_running()

    def _restore_ini(self, stopped: bool) -> None:
        """citect.ini as found when the runtime only added its window positions (and the
        batch its [CtEdit] Run); then [CtEdit] Run = ExampleSA, the shared default."""
        from ctgedit.backend import runtime
        from ctgedit.format.fileio import write_atomic

        if self.ini_path and self.ini_before is not None and stopped:
            try:
                with open(self.ini_path, "rb") as fh:
                    now = fh.read()
                if now != self.ini_before:
                    diff = ini_differences(self.ini_before, now)
                    if diff:
                        self.note("citect.ini left as it is: %s changed during the batch" % diff)
                    else:
                        write_atomic(self.ini_path, self.ini_before)
                        self.note(
                            "citect.ini back to its bytes before the batch (the runtime "
                            "had rewritten its window positions)"
                        )
            except OSError as e:
                self.note("citect.ini NOT restored: %s" % e)
        try:
            sa = project_dir("ExampleSA")
            if not runtime.runs_project(sa):
                runtime.set_run_project(sa)
                self.note("[CtEdit] Run set to ExampleSA (it was %r before the batch)" % self.run_before)
        except (SystemExit, OSError) as e:
            self.note("[CtEdit] Run NOT set back to ExampleSA: %s" % e)

    def _clean_overrides(self, stopped: bool) -> None:
        """Remove the Override elements this batch created (TagSetOverride*) from the IO
        server's persistence caches, for tags whose OverrideMode is back at 0: what a tag
        that was never overridden looks like there. Pre-existing ones are left alone."""
        if self.overrides_before is None or not self.data_dir:
            return
        new = new_overrides(self.overrides_before, override_inventory(self.data_dir))
        if not new:
            self.note("IO server caches: no new Override element")
            return
        listing = "; ".join("%s: %s" % (f, ", ".join(sorted(t))) for f, t in new.items())
        why = (
            "--keep-overrides"
            if self.keep_overrides
            else "the runtime still runs"
            if not stopped
            # a runtime found running may hold another session's overrides in memory
            else "the batch did not start the runtime"
            if not self.started
            else ""
        )
        if why:
            self.note("IO server caches: new Override elements left (%s): %s" % (why, listing))
            return
        backup = os.path.join(state_dir(), "cachebak", time.strftime("%Y%m%d-%H%M%S"))
        for f, tags in sorted(new.items()):
            idle = [t for t, m in tags.items() if (m or "0").strip() == "0"]
            busy = sorted(set(tags) - set(idle))
            try:
                gone = remove_override_elements(os.path.join(self.data_dir, f), idle, backup)
            except (OSError, RuntimeError, ValueError) as e:
                self.note("IO server cache %s: Override elements NOT removed (%s)" % (f, e))
                continue
            self.note(
                "IO server cache %s: removed the Override element of %s (backup in %s)%s"
                % (
                    f,
                    ", ".join(gone) or "nothing",
                    backup,
                    "; left (OverrideMode not 0): %s" % ", ".join(busy) if busy else "",
                )
            )

    def __exit__(self, *exc):
        from ctgedit.backend import runtime

        try:
            if self.cap:
                self.cap.close()
                self.cap = None
            if runtime.running_processes():
                how = runtime.stop()
                self.note("runtime stopped (%s)" % how)
            stopped = not runtime.running_processes()
            if not stopped:
                self.note("RUNTIME STILL RUNNING after the stop: %s" % runtime.running_processes())
            self._restore_ini(stopped)
            self._clean_overrides(stopped)
        finally:
            if self.watch:
                self.watch.stop()
                for a in self.watch.answered:
                    self.journal.append("demo box: " + a)
                for f in self.watch.foreign:
                    self.journal.append("dialog left alone: " + f)
            self.lock.__exit__(*exc)
            with open(_journal_path(), "a", encoding="utf-8") as fh:
                fh.write("\n".join(self.journal) + "\n")
        return False


# --------------------------------------------------------------------------- capture
def read_all(cap: CtCap, tags: "OrderedDict[str, dict]") -> Dict[str, dict]:
    """Every tag through ctTagReadEx, one after the other (about 12 ms a tag)."""
    out = {}
    for meta in tags.values():
        r = cap.read(meta["name"])
        if not r.get("ok") and meta.get("tag") and meta["tag"].lower() != meta["name"].lower():
            r2 = cap.read(meta["tag"])
            if r2.get("ok"):
                r = r2
        out[meta["name"]] = snapshot_entry(meta, r)
    return out


def _int_answer(r: Optional[dict]) -> Optional[int]:
    """The integer a CtAPI answer holds, None when it failed or holds none."""
    if not r or not r.get("ok"):
        return None
    try:
        return int(float(str(r.get("value", "")).strip()))
    except ValueError:
        return None


def unix_iso(seconds: int, ms: int = 0) -> Optional[str]:
    """Unix seconds (UTC, an alarm's OnTime / Time) as an ISO 8601 UTC time."""
    if not seconds:
        return None
    try:
        t = _dt.datetime(1970, 1, 1) + _dt.timedelta(seconds=int(seconds), milliseconds=int(ms))
    except OverflowError, ValueError:
        return None
    return t.isoformat(timespec="milliseconds") + "Z"


def alarm_states(alarms: List[str], items: List[dict]) -> "OrderedDict[str, dict]":
    """Tag -> state of every active alarm."""
    out: "OrderedDict[str, dict]" = OrderedDict()
    k = len(ALARM_PROPS)
    for i, tag in enumerate(alarms):
        rs = items[i * k : (i + 1) * k]
        if len(rs) < k:
            break
        vals = {p: _int_answer(r) for p, r in zip(ALARM_PROPS, rs)}
        bad = [
            r for p, r in zip(ALARM_PROPS, rs) if p in ("On", "Ack", "Disabled", "State") and vals[p] is None
        ]
        if bad:
            r = bad[0]
            out[tag] = {
                "error": int(r.get("error", 0) or 0)
                if not r.get("ok")
                else "not a number: %r" % r.get("value")
            }
            continue
        if (vals["On"], vals["Ack"], vals["Disabled"], vals["State"]) == (0, 1, 0, 0):
            continue
        e: "OrderedDict[str, object]" = OrderedDict(
            [
                ("on", vals["On"]),
                ("ack", vals["Ack"]),
                ("disabled", vals["Disabled"]),
                ("state", vals["State"]),
            ]
        )
        on_t = unix_iso(vals["OnTime"] or 0, vals["Millisec"] or 0)
        if on_t:
            e["onTime"] = on_t
        t = unix_iso(vals["Time"] or 0)
        if t:
            e["time"] = t
        out[tag] = e
    return out


def live_state_requests(doc: dict, frame: dict) -> List[Tuple[str, dict]]:
    """The ctsim requests that set an engine to a frame's alarm state."""
    out: List[Tuple[str, dict]] = []
    alarms = frame.get("alarms") or {}
    for tag in doc.get("alarmDefs") or {}:
        st = alarms.get(tag)
        if isinstance(st, dict) and "error" in st:
            continue
        out.append(("ctsim/setAlarm", {"name": tag, "state": st if st is not None else "off"}))
    for name, fields in (frame.get("equipment") or {}).items():
        f = {k: v for k, v in (fields or {}).items() if isinstance(v, str)}
        if f:
            out.append(("ctsim/setEquipment", {"name": name, "fields": f}))
    return out


def equipment_call(name: str, field: str) -> str:
    """The EquipGetProperty call a frame reads an equipment's runtime field with (mode 1, the
    client's cache, as the ExampleSA pages call it)."""
    return 'EquipGetProperty(%s, %s, 1, "")' % (cicode_str(name), cicode_str(field))


class EquipmentReader:
    """The runtime properties (EQUIP_FIELDS) of a page's equipment, one ctCicode call each
    (about 1 ms). Warmed up when made: EquipGetProperty in mode 1 answers 'Unknown' the
    first time an equipment is asked (it fills the client's cache), as a page's first scan
    does."""

    def __init__(self, cap: CtCap, names: List[str], warm: float = 1.0):
        self.cap, self.names = cap, list(names)
        if self.names:
            self.read()
            time.sleep(warm)

    def read(self) -> "OrderedDict[str, dict]":
        out: "OrderedDict[str, dict]" = OrderedDict()
        for n in self.names:
            e: "OrderedDict[str, object]" = OrderedDict()
            for f in EQUIP_FIELDS:
                r = self.cap.cicode(equipment_call(n, f))
                e[f] = r.get("value", "") if r.get("ok") else {"error": r.get("error")}
            out[n] = e
        return out


class Snapshotter:
    """Every tag of a page at one moment: a CtAPI poll list (ctListAddEx, 250 ms like the
    page scan) read with one ctListRead (ctTagReadEx tag by tag takes seconds, too long for
    one moment). A tag the list cannot read is read by its variable tag name."""

    def __init__(
        self, cap: CtCap, tags: "OrderedDict[str, dict]", name: str, alarms: Optional[List[str]] = None
    ):
        self.cap, self.tags, self.name = cap, tags, name
        # the alarms' properties go into the same list: one ctListRead reads the tags and the
        # alarm states at one moment
        self.alarms = list(alarms or [])
        refs = [m["name"] for m in tags.values()]
        refs += ["%s.%s" % (a, p) for a in self.alarms for p in ALARM_PROPS]
        adds = cap.list_new(name, refs)
        n = len(tags)
        self.not_added = [m["name"] for m, a in zip(tags.values(), adds[:n]) if not a.get("ok")]
        self.alarms_not_added = sorted(
            {ref.rsplit(".", 1)[0] for ref, a in zip(refs[n:], adds[n:]) if not a.get("ok")}, key=str.lower
        )

    def read(self, names=None) -> Dict[str, dict]:
        return self.read_full(names)[0]

    def read_full(self, names=None) -> Tuple[Dict[str, dict], Dict[str, dict]]:
        """(tag snapshot, alarm states) of one ctListRead; the alarm states = alarm_states."""
        want = {n.lower() for n in names} if names is not None else None
        items = self.cap.list_read(self.name)
        out = {}
        n = len(self.tags)
        for meta, r in zip(self.tags.values(), items[:n]):
            if want is not None and meta["name"].lower() not in want:
                continue
            if not r.get("ok") and meta.get("tag") and meta["tag"].lower() != meta["name"].lower():
                r2 = self.cap.read(meta["tag"])
                if r2.get("ok"):
                    r = r2
            out[meta["name"]] = snapshot_entry(meta, r)
        return out, alarm_states(self.alarms, items[n:])

    def check(self, steady: List[str]) -> dict:
        """The list read against ctTagReadEx tag by tag (once per page): the tags that read
        differently, leaving out the self-changing ones (``steady`` = those to compare)."""
        fast = self.read()
        slow = read_all(self.cap, self.tags)
        keep = {n.lower() for n in steady}
        diff = [k for k in differing(fast, slow) if k.lower() in keep]
        return OrderedDict(
            [("tags", len(fast)), ("compared", len(keep)), ("differing", diff), ("notAdded", self.not_added)]
        )

    def close(self) -> None:
        try:
            self.cap.list_free(self.name)
        except CtApiError:
            pass


def _value_key(e: dict):
    return (e.get("text"), e.get("quality"), e.get("qualityWord"), e.get("error"))


def differing(a: Dict[str, dict], b: Dict[str, dict]) -> List[str]:
    return [k for k in a if _value_key(a[k]) != _value_key(b.get(k, {}))]


def pixels_differing(a, b) -> int:
    """0 when two captures are identical, else the number of differing pixels."""
    if a.size != b.size:
        return a.size[0] * a.size[1]
    from PIL import ImageChops

    diff = ImageChops.difference(a, b).convert("L")
    hist = diff.histogram()
    return sum(hist[1:])


class PageWindow:
    """A page opened in its own runtime window (WinNewAt at x, y, no caption)."""

    def __init__(
        self,
        cap: CtCap,
        page: str,
        associations: Optional[Dict[str, str]] = None,
        x: int = 0,
        y: int = 0,
        mode: int = WINDOW_MODE,
    ):
        self.cap, self.page = cap, page
        self.calls: List[dict] = []
        for k, v in (associations or {}).items():
            stmt = "Ass(-2, %s, %s, 0)" % (cicode_str(k), cicode_str(v))
            self.calls.append({"call": stmt, "result": cap.cicode(stmt)})
        stmt = "WinNewAt(%s, %d, %d, %d)" % (cicode_str(page), x, y, mode)
        r = cap.cicode(stmt)
        self.calls.append({"call": stmt, "result": r})
        if not r.get("ok"):
            raise RuntimeError("%s failed: %s" % (stmt, r))
        self.number = int(float(r["value"]))
        if self.number < 0:
            raise RuntimeError("%s returned %s" % (stmt, r["value"]))
        try:
            h = cap.cicode("WinGetWndHnd()", self.number)
            self.hwnd = int(float(h.get("value", "0") or 0)) & 0xFFFFFFFF
            shown = cap.cicode("PageInfo(0)", self.number).get("value", "")
            self.shown = ("!" + shown.strip()) if shown.startswith(" ") and shown.strip() else shown.strip()
            if not self.hwnd or not _user32.IsWindow(self.hwnd):
                raise RuntimeError("no window handle for window %d (%s)" % (self.number, h))
        except BaseException:
            self.close()  # the window opened: do not leave it behind
            raise

    def info(self) -> dict:
        d = window_info(self.hwnd)
        d["number"] = self.number
        d["page"] = self.shown
        return d

    def close(self) -> None:
        try:
            self.cap.cicode("WinFree()", self.number)
        except Exception:  # noqa: BLE001
            pass


#: scenario quality -> (Cicode function, the OPC general quality it gives: 0 bad, 1 uncertain, 3 good)
_OVERRIDE_QUALITY = {
    "bad": ("TagSetOverrideBad", 0),
    "uncertain": ("TagSetOverrideUncertain", 1),
    "good": ("TagSetOverrideGood", 3),
}


def _general(q: int) -> int:
    return (q & 0xFF) >> 6


def quality_held(entry: dict, quality: str) -> bool:
    """Does a snapshot entry show ``quality`` set through the Override element (general
    quality as asked, override flag on)?"""
    if not entry or "error" in entry:
        return False
    q = _entry_word(entry)
    return q is not None and _general(q) == _OVERRIDE_QUALITY[quality][1] and bool(q & (1 << 16))


def _entry_word(entry: dict) -> Optional[int]:
    """The 64-bit quality of a snapshot entry (its qualityWord, else its quality name)."""
    w = entry.get("qualityWord")
    if w:
        return int(w, 16)
    name = entry.get("quality")
    for q, n in _QNAMES.items():
        if n == name:
            return q
    if isinstance(name, str) and name.startswith("0x"):
        return int(name, 16)
    return None


def _poll(test, timeout: float, step: float = 0.1) -> bool:
    end = time.time() + timeout
    while True:
        if test():
            return True
        if time.time() >= end:
            return False
        time.sleep(step)


def set_quality(
    cap: CtCap, tag: str, quality: str, mode: int = 1, timeout: float = 3.0, tries: int = 3
) -> List[dict]:
    """Set a tag's quality through its Override element."""
    fn, general = _OVERRIDE_QUALITY[quality]
    out = [{"set": tag + ".OverrideMode", "value": mode, "result": cap.write(tag + ".OverrideMode", mode)}]

    def mode_is() -> bool:
        r = cap.read(tag + ".OverrideMode")
        try:
            return bool(r.get("ok")) and int(float(r.get("value", ""))) == int(mode)
        except ValueError:
            return False

    out[-1]["readBack"] = _poll(mode_is, timeout)
    stmt = "%s(%s)" % (fn, cicode_str(tag))

    def held() -> bool:
        r = cap.read(tag)
        if not r.get("ok"):
            return False
        q = quality_word(r)
        return _general(q) == general and bool(q & (1 << 16))

    for n in range(1, tries + 1):
        step = {"call": stmt, "result": cap.cicode(stmt)}
        out.append(step)
        if _poll(held, timeout):
            step["held"] = True
            break
        step["held"] = False
    return out


def apply_step(cap: CtCap, step: dict, orig: Dict[str, dict], window: int = 0) -> List[dict]:
    """Run one frame's writes on the live runtime."""
    done = []
    for tag, v in (step.get("set") or {}).items():
        if tag not in orig:
            orig[tag] = {"value": cap.read(tag)}
        done.append({"set": tag, "value": v, "result": cap.write(tag, v)})
    for tag, q in (step.get("quality") or {}).items():
        if tag not in orig:
            orig[tag] = {"value": cap.read(tag)}
        orig[tag].setdefault("mode", cap.read(tag + ".OverrideMode"))
        done += set_quality(cap, tag, q, int(step.get("overrideMode", 1)))
    for stmt in step.get("call") or []:
        w = int(step.get("window", window))
        done.append({"call": stmt, "window": w, "result": cap.cicode(stmt, w)})
    return done


def restore(cap: CtCap, orig: Dict[str, dict]) -> List[dict]:
    """Every touched tag back: its override mode first, then its value. Never raises: a
    write that fails is recorded (``ok`` false) and the next tag is tried."""
    out = []

    def put(name: str, value) -> None:
        try:
            r = cap.write(name, value)
        except CtApiError as e:
            r = {"ok": False, "error": str(e)}
            if not cap.alive():
                try:
                    cap.restart()
                except CtApiError as e2:
                    r["restart"] = str(e2)
        out.append({"set": name, "value": value, "result": r})

    for tag, o in orig.items():
        if "mode" in o:
            m = o["mode"]
            put(tag + ".OverrideMode", m.get("value", "0") if m.get("ok") else "0")
        v = o["value"]
        if v.get("ok"):
            put(tag, v["value"])
    return out


def self_changing(snap: Snapshotter, seconds: float = 5.0, step: float = 0.1) -> List[str]:
    """The page's tags that change on their own (the runtime's simulation Cicode)."""
    first = snap.read()
    seen = set()
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(step)
        seen.update(differing(first, snap.read()))
    return [m["name"] for m in snap.tags.values() if m["name"] in seen]


def wait_edge(snap: Snapshotter, names, timeout: float = 2.5, step: float = 0.03) -> bool:
    """Wait until one of ``names`` changes (the start of a quiet period of the runtime's
    simulation); False when none changed within ``timeout``."""
    if not names:
        return False
    base = snap.read(names)
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(step)
        if differing(base, snap.read(names)):
            return True
    return False


def _override_call(tag: str, quality: str) -> str:
    return "%s(%s)" % (_OVERRIDE_QUALITY[quality][0], cicode_str(tag))


def sync_quality(
    cap: CtCap, snap: Snapshotter, asked: Dict[str, str], window: float = 1.2, step: float = 0.03
) -> bool:
    """Set the override qualities again just before a capture."""
    for tag, q in asked.items():
        cap.cicode(_override_call(tag, q))
    end = time.time() + window
    while time.time() < end:
        cur = snap.read(list(asked))
        undone = [t for t, q in asked.items() if not quality_held(cur.get(t), q)]
        if undone:
            for t in undone:
                cap.cicode(_override_call(t, asked[t]))
            return True
        time.sleep(step)
    return False


def _alarm_moves(a: Dict[str, dict], b: Dict[str, dict]) -> List[str]:
    """The alarms whose state differs between two alarm_states results (the times aside)."""

    def key(e):
        return (
            None
            if e is None
            else (e.get("on"), e.get("ack"), e.get("disabled"), e.get("state"), e.get("error"))
        )

    return [t for t in OrderedDict.fromkeys(list(a) + list(b)) if key(a.get(t)) != key(b.get(t))]


#: captures per frame and the time between them (``run --captures 4 --capture-gap 0.25``): four
#: captures 250 ms apart span both flash phases of the runtime's 0.5 s flash
CAPTURE_COUNT = 4
CAPTURE_GAP = 0.25


def capture_frame(
    cap: CtCap,
    win: PageWindow,
    snap: Snapshotter,
    frame_dir: str,
    name: str,
    ramping: List[str],
    written: List[str],
    asked: Optional[Dict[str, str]] = None,
    retries: int = 6,
    show_delay: float = 0.4,
    second_gap: Optional[float] = None,
    equipment: Optional[EquipmentReader] = None,
    more: Optional[int] = None,
) -> dict:
    """Capture one frame."""
    asked = asked or {}
    if more is None:
        more = max(1, CAPTURE_COUNT - 1)
    if second_gap is None:
        second_gap = CAPTURE_GAP
    watch = list(OrderedDict.fromkeys(ramping + written + list(asked)))
    attempt = 0
    requality = False
    while True:
        attempt += 1
        synced = wait_edge(snap, ramping) if ramping else False
        if asked:
            requality = sync_quality(cap, snap, asked) or requality
        t_wait = time.time()
        eq_a = equipment.read() if equipment else OrderedDict()
        time.sleep(max(0.0, show_delay - (time.time() - t_wait)))
        a, al_a = snap.read_full()
        t_read = time.time()
        img1 = capture_client(win.hwnd)
        t_capture = time.time()
        b, al_b = snap.read_full()
        moved = [k for k in watch if k in a and _value_key(a[k]) != _value_key(b.get(k, {}))]
        al_moved = _alarm_moves(al_a, al_b)
        # an asked quality the runtime undid again before the snapshot (its simulation wrote
        # the tag) is retried like a moving tag
        q_lost = [t for t, q in asked.items() if not quality_held(a.get(t), q)]
        if not (moved or al_moved or q_lost) or attempt >= retries:
            break
    eq_b = equipment.read() if equipment else OrderedDict()
    extra = []  # (image, time) of the later captures
    t_prev = t_capture
    for _k in range(max(1, more)):
        time.sleep(max(0.0, second_gap - (time.time() - t_prev)))
        extra.append((capture_client(win.hwnd), time.time()))
        t_prev = extra[-1][1]
    img2, t2 = extra[0]
    c, al_c = snap.read_full()
    p1 = os.path.join(frame_dir, name + ".png")
    size = save_png(img1, p1)
    fr = OrderedDict(
        [
            ("name", name),
            ("captured", _dt.datetime.fromtimestamp(t_capture).isoformat(timespec="milliseconds")),
            ("attempts", attempt),
            ("synced", synced),
            ("snapshotToCapture", round(t_capture - t_read, 3)),
            ("capture", os.path.basename(p1)),
        ]
    )
    if asked:
        fr["qualitySetAgain"] = requality
    kept = [img1]
    names = [os.path.basename(p1)]
    delays = []
    for k, (img, t_k) in enumerate(extra):
        if all(pixels_differing(img, other) for other in kept):
            pk = os.path.join(frame_dir, "%s_%s.png" % (name, "bcde"[k] if k < 4 else str(k)))
            size += save_png(img, pk)
            kept.append(img)
            names.append(os.path.basename(pk))
            delays.append(round(t_k - t_capture, 3))
    nd = pixels_differing(img1, img2)
    fr["capture2Delay"] = round(t2 - t_capture, 3)
    fr["pixelsChangedInCapture2"] = nd
    fr["capturesTaken"] = 1 + len(extra)
    fr["captureGap"] = round(float(second_gap), 3)
    fr["capturesSpan"] = round(extra[-1][1] - t_capture, 3)
    if len(names) > 1:
        fr["capture2"] = names[1]
        fr["captures"] = names
        fr["captureDelays"] = delays
    else:
        fr["capturesIdentical"] = True
    fr["bytes"] = size
    if moved:
        fr["unstable"] = OrderedDict(
            (k, {"atCapture": a[k].get("text"), "after": b[k].get("text")}) for k in moved
        )
    during = [k for k in differing(a, b) if k not in moved]
    if during:
        fr["changedDuringCapture"] = OrderedDict((k, b[k].get("text")) for k in during)
    later = differing(a, c)
    if later:
        fr["changedByCapture2"] = OrderedDict((k, c[k].get("text")) for k in later)
    if al_moved:
        fr["alarmsUnstable"] = OrderedDict(
            (t, {"atCapture": al_a.get(t), "after": al_b.get(t)}) for t in al_moved
        )
    al_later = _alarm_moves(al_a, al_c)
    if al_later:
        fr["alarmsChangedByCapture2"] = OrderedDict((t, al_c.get(t)) for t in al_later)
    eq_moved = [n for n in eq_a if eq_a[n] != eq_b.get(n)]
    if eq_moved:
        fr["equipmentChangedDuringCapture"] = OrderedDict((n, eq_b.get(n)) for n in eq_moved)
    fr["snapshot"] = a
    fr["alarms"] = al_a
    fr["equipment"] = eq_a
    fr["_t"] = t_capture
    return fr


# --------------------------------------------------------------------------- the batch
def project_dir(name: str) -> str:
    from ctgedit.format.projects import master_projects
    from ctgedit.io.citect_config import live_user_dir

    user = live_user_dir()
    if not user:
        raise SystemExit("the active install's User folder was not found")
    d = master_projects(user).get(name.lower()) or os.path.join(user, name)
    if not os.path.isdir(d):
        raise SystemExit("project %s not found (%s)" % (name, d))
    return d


def load_plan(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _sha1(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return None


def fast_display_file(project: str, page: str) -> Optional[dict]:
    """The page's .ctF (what the runtime draws with [Animator] FastDisplay on, the default)."""
    for f in os.listdir(project):
        if f.lower() == (page + ".ctf").lower():
            return {"file": f, "sha1": _sha1(os.path.join(project, f))}
    return None


def font_smoothing() -> Optional[bool]:
    """SPI_GETFONTSMOOTHING: whether text is anti-aliased in this session (None when the
    query fails)."""
    b = wt.BOOL()
    if not _user32.SystemParametersInfoW(0x004A, 0, ctypes.byref(b), 0):
        return None
    return bool(b.value)


def cursor_pos() -> Optional[List[int]]:
    """The mouse cursor's screen position, None when the session has no input desktop (a
    locked or disconnected RDP session: GetCursorPos fails with access denied)."""
    pt = wt.POINT()
    if not _user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return [pt.x, pt.y]


def page_title(model: ProjectModel, page: str) -> str:
    for r in model._rows(model.dir, "pages"):
        if _f(r, "NAME").lower() == page.lower():
            return _f(r, "TITLE")
    return ""


def _scenario_value(v):
    if isinstance(v, str):
        try:
            f = float(v)
            return int(f) if f.is_integer() and "." not in v else f
        except ValueError:
            return v
    return v


def run_case(
    cap: CtCap,
    session: Optional[RuntimeSession],
    model: ProjectModel,
    page: str,
    win: PageWindow,
    snap: Snapshotter,
    case: dict,
    out_dir: str,
    settle: float,
    ramping: List[str],
    equipment: Optional[EquipmentReader] = None,
) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, "*.png")):
        os.remove(old)
    orig: Dict[str, dict] = {}
    tags = snap.tags
    active_quality: "OrderedDict[str, str]" = OrderedDict()  # overrides this case set
    # a tag already in override mode when the case starts (left by someone) is not what
    # the frames say: report it (the restore puts it back as found)
    overridden = []
    for tag in OrderedDict.fromkeys(
        t for fr in case["frames"] for t in list(fr.get("set") or {}) + list(fr.get("quality") or {})
    ):
        r = cap.read(tag + ".OverrideMode")
        if r.get("ok") and r.get("value", "0").strip() not in ("0", ""):
            overridden.append(tag)
    if overridden:
        log("  ALREADY IN OVERRIDE MODE at the case start: %s" % overridden)
        if session:
            session.note(
                "tags already in override mode at the start of %s: %s" % (case.get("name"), overridden)
            )
    t0 = time.time()
    frames, steps = [], []
    try:
        for fr in case["frames"]:
            if session:
                session.check_dialogs()
            at = round(time.time() - t0, 3)
            done = apply_step(cap, fr, orig, win.number)
            if fr.get("set"):
                steps.append(
                    {"at": at, "set": OrderedDict((tag, _scenario_value(v)) for tag, v in fr["set"].items())}
                )
            if fr.get("quality"):
                steps.append({"at": at, "quality": dict(fr["quality"])})
            for stmt in fr.get("call") or []:
                steps.append({"at": at, "call": stmt, "window": int(fr.get("window", win.number))})
            for tag, q in (fr.get("quality") or {}).items():
                active_quality[tag] = q
            time.sleep(float(fr.get("settle", settle)))
            frame = capture_frame(
                cap,
                win,
                snap,
                out_dir,
                fr["name"],
                ramping,
                [t for t in orig if t.lower() in tags],
                dict(active_quality),
                equipment=equipment,
            )
            frame["at"] = round(frame.pop("_t") - t0, 3)
            frame["description"] = fr.get("description", "")
            frame["steps"] = done
            held = {}
            for tag, v in (fr.get("set") or {}).items():
                e = frame["snapshot"].get(tag)
                if e is not None:
                    held[tag] = _value_matches(e.get("value"), v)
            if held:
                frame["writesHeld"] = held
            qheld = OrderedDict()
            for tag, q in (fr.get("quality") or {}).items():
                qheld[tag] = quality_held(frame["snapshot"].get(tag), q)
            if qheld:
                frame["qualityHeld"] = qheld
            frames.append(frame)
            log(
                "  frame %s: %d attempt(s), %d bytes%s%s"
                % (
                    fr["name"],
                    frame["attempts"],
                    frame["bytes"],
                    ", unstable %d" % len(frame["unstable"]) if frame.get("unstable") else "",
                    ", QUALITY NOT HELD %s" % [t for t, ok in qheld.items() if not ok]
                    if not all(qheld.values())
                    else "",
                )
            )
    finally:
        if not cap.alive():
            # a timeout killed the session: the tags still have to go back
            log("  the CtAPI session died; a new one puts the tags back")
            try:
                cap.restart()
            except CtApiError as e:
                log("  no new CtAPI session: %s" % e)
        restored = restore(cap, orig)
        failed = [r["set"] for r in restored if not (r.get("result") or {}).get("ok")]
        if failed:
            log("  RESTORE FAILED for %s" % failed)
            if session:
                session.note("restore failed for %s (case %s)" % (failed, case.get("name")))
    scenario = OrderedDict(
        [
            ("format", "ctsim-scenario/0"),
            ("project", model.name),
            ("description", case.get("description", "")),
            ("page", {"window": win.number, "name": page}),
            ("steps", steps),
        ]
    )
    return {"scenario": scenario, "frames": frames, "restore": restored, "overriddenAtStart": overridden}


def recorder_info() -> dict:
    """Which version of this tool recorded a case: its git commit and whether the file had
    changes on top of it."""
    out = OrderedDict([("tool", "tools/sim_page_capture.py")])
    try:
        sha = subprocess.run(
            ["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
        dirty = subprocess.run(
            [
                "git",
                "-C",
                ROOT,
                "status",
                "--porcelain",
                "--",
                "tools/sim_page_capture.py",
                "tools/sim_page_capture_plan.json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        out["commit"] = sha or None
        out["modified"] = bool(dirty)
    except OSError, subprocess.SubprocessError:
        out["commit"] = None
    return out


def _value_matches(got, want) -> bool:
    try:
        return abs(float(got) - float(want)) <= 1e-6 * max(1.0, abs(float(want)))
    except TypeError, ValueError:
        return str(got) == str(want)


def run_batch(
    plan: dict, out_root: str, only: List[str], session: Optional[RuntimeSession], cap: CtCap, settle: float
) -> List[dict]:
    model = ProjectModel(project_dir(plan.get("project", "ExampleSA")))
    ct = ctsim_exe()
    version = runtime_version()
    version["project"] = model.name
    version["mode"] = "demo"
    version["install"] = os.path.dirname(model.dir)
    results = []
    recorder = recorder_info()
    dpi_aware()
    # the mouse away from every page area (no hover state); put back afterwards. In a session
    # without an input desktop (locked RDP) both calls fail and the cursor is unknown
    user_cursor = cursor_pos()
    park = list(plan.get("cursor", (1919, 1199)))
    parked = bool(_user32.SetCursorPos(*park))
    try:
        return _run_pages(
            plan, out_root, only, session, cap, settle, model, ct, version, recorder, parked, results
        )
    finally:
        if parked and user_cursor:
            _user32.SetCursorPos(*user_cursor)


def _run_pages(
    plan, out_root, only, session, cap, settle, model, ct, version, recorder, parked, results
) -> List[dict]:
    for spec in plan["pages"]:
        page = spec["page"]
        wanted = [
            c["name"]
            for c in spec["cases"]
            if not only or page in only or ("%s/%s" % (page, c["name"])) in only
        ]
        if not wanted:
            continue
        assoc = spec.get("associations") or {}
        tags, equip = page_refs(model, page, ct, assoc)
        classes = page_classes(model, page)
        alarms = [a["tag"] for a in model.alarms.values()]
        log(
            "page %s: %d tags, %d equipment, %d alarms, classes %s"
            % (page, len(tags), len(equip), len(alarms), classes)
        )
        win = PageWindow(cap, page, assoc, *spec.get("at", (0, 0)))
        snap = None
        try:
            time.sleep(float(spec.get("open_settle", 4.0)))
            snap = Snapshotter(cap, tags, "page%d" % win.number, alarms)
            if snap.alarms_not_added:
                log(" alarm properties the list refused: %s" % snap.alarms_not_added[:10])
            equipment = EquipmentReader(cap, [e["name"] for e in equip.values()])
            ramping = self_changing(snap)
            log(" self-changing tags: %s" % ramping)
            snap_check = snap.check([m["name"] for m in tags.values() if m["name"] not in ramping])
            log(
                " poll list vs ctTagReadEx: %d of %d steady tags differ %s"
                % (len(snap_check["differing"]), snap_check["compared"], snap_check["differing"][:5])
            )
            cursor = cursor_pos()
            for case in spec["cases"]:
                if case["name"] not in wanted:
                    continue
                log(" case %s/%s" % (page, case["name"]))
                cdir = os.path.join(out_root, model.name, page, case["name"])
                res = run_case(cap, session, model, page, win, snap, case, cdir, settle, ramping, equipment)
                doc = OrderedDict()
                doc["format"] = FORMAT
                doc["tag"] = "live"
                doc["recorded"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
                doc["description"] = case.get("description", "")
                doc["recorder"] = recorder
                doc["runtime"] = version
                doc["page"] = OrderedDict(
                    [
                        ("name", page),
                        ("title", page_title(model, page)),
                        ("classes", classes),
                        (
                            "rdb",
                            {"file": page + ".RDB", "sha1": _sha1(os.path.join(model.dir, page + ".RDB"))},
                        ),
                        (
                            "ctg",
                            {"file": page + ".ctg", "sha1": _sha1(os.path.join(model.dir, page + ".ctg"))},
                        ),
                        ("ctf", fast_display_file(model.dir, page)),
                    ]
                )
                info = win.info()
                doc["window"] = OrderedDict(
                    [
                        ("number", win.number),
                        ("open", [c["call"] for c in win.calls]),
                        ("mode", spec.get("mode", WINDOW_MODE)),
                        ("shows", win.shown),
                        ("rect", info["rect"]),
                        ("client", info["client"]),
                        ("screen", screen_rect()),
                        ("dpi", info["dpi"]),
                        ("class", info["class"]),
                        ("caption", info["caption"]),
                        ("style", info["style"]),
                        ("cursor", cursor),
                        ("cursorParked", parked),
                        ("fontSmoothing", font_smoothing()),
                    ]
                )
                doc["associations"] = assoc
                doc["scenario"] = res["scenario"]
                doc["selfChanging"] = ramping
                doc["snapshotRead"] = OrderedDict(
                    [("api", "ctListAddEx + ctListRead"), ("pollMs", 250), ("check", snap_check)]
                )
                doc["tags"] = OrderedDict(
                    (
                        m["name"],
                        OrderedDict(
                            [
                                ("kind", m["kind"]),
                                ("tag", m["tag"]),
                                ("type", m["type"]),
                                ("sources", m["sources"]),
                            ]
                        ),
                    )
                    for m in tags.values()
                )
                doc["alarmsRead"] = OrderedDict(
                    [
                        ("api", "ctListAddEx + ctListRead, in the tags' list"),
                        ("properties", list(ALARM_PROPS)),
                        ("checked", len(alarms)),
                        ("notAdded", snap.alarms_not_added),
                        (
                            "absent",
                            "a checked alarm a frame leaves out is off, acknowledged and "
                            "enabled (On 0, Ack 1, Disabled 0, State 0)",
                        ),
                    ]
                )
                doc["alarmDefs"] = OrderedDict(
                    (
                        a["tag"],
                        OrderedDict(
                            (k, a[k])
                            for k in ("kind", "equipment", "item", "category", "priority", "project")
                        ),
                    )
                    for a in model.alarms.values()
                )
                doc["equipmentRead"] = OrderedDict(
                    [
                        ("call", equipment_call("<equipment>", "<field>")),
                        ("fields", list(EQUIP_FIELDS)),
                        ("warmUp", True),
                    ]
                )
                doc["equipmentDefs"] = OrderedDict(
                    (
                        e["name"],
                        OrderedDict(
                            [
                                (
                                    "scheduled",
                                    model.equipment[k].get("SCHEDULED", "").upper() in ("TRUE", "1"),
                                ),
                                ("defstate", model.equipment[k].get("DEFSTATE", "")),
                                ("sources", e["sources"]),
                            ]
                        ),
                    )
                    for k, e in equip.items()
                )
                doc["overriddenAtStart"] = res["overriddenAtStart"]
                doc["frames"] = res["frames"]
                doc["restore"] = res["restore"]
                with open(os.path.join(cdir, "case.json"), "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(doc, fh, indent=1, ensure_ascii=False)
                    fh.write("\n")
                results.append(
                    {
                        "page": page,
                        "case": case["name"],
                        "frames": len(res["frames"]),
                        "bytes": sum(f["bytes"] for f in res["frames"]),
                        "unstable": sum(1 for f in res["frames"] if f.get("unstable")),
                        "alarms": [len(f.get("alarms") or {}) for f in res["frames"]],
                        "writesHeld": [f.get("writesHeld") for f in res["frames"]],
                        "qualityHeld": [f.get("qualityHeld") for f in res["frames"]],
                        "restoreFailed": [
                            r["set"] for r in res["restore"] if not (r.get("result") or {}).get("ok")
                        ],
                    }
                )
        finally:
            if snap:
                snap.close()
            win.close()
    return results


# --------------------------------------------------------------------------- commands
def cmd_classes(args) -> int:
    model = ProjectModel(project_dir(args.project))
    pages = args.pages or [p["page"] for p in load_plan(args.plan)["pages"]]
    out = OrderedDict((p, page_classes(model, p)) for p in pages)
    print(json.dumps(out, indent=1))
    return 0


def cmd_tags(args) -> int:
    model = ProjectModel(project_dir(args.project))
    assoc = dict(a.split("=", 1) for a in (args.assoc or []))
    tags, equip = page_refs(model, args.page, ctsim_exe(args.ctsim), assoc)
    for m in tags.values():
        print("%-6s %-8s %-60s %s" % (m["kind"], m["type"], m["name"], ",".join(m["sources"])))
    for e in equip.values():
        print("%-15s %-60s %s" % ("equipment", e["name"], ",".join(e["sources"])))
    print(
        "%d tags, %d equipment, %d alarms checked" % (len(tags), len(equip), len(model.alarms)),
        file=sys.stderr,
    )
    return 0


def cmd_hold(args) -> int:
    """Take the lock, bring the runtime up and keep both until ``hold.stop`` appears (or the
    time runs out), so ``exec`` / ``run`` calls in between share one batch."""
    st_path = os.path.join(state_dir(), "hold.json")
    stop = os.path.join(state_dir(), "hold.stop")
    if os.path.exists(stop):
        os.remove(stop)
    journal: List[str] = []
    dpi_aware()
    try:
        with RuntimeSession(project_dir(args.project), journal, keep_overrides=args.keep_overrides) as rs:
            with open(st_path, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "ready": True, "since": _now(), "project": args.project}, fh)
            end = time.time() + args.minutes * 60
            while time.time() < end and not os.path.exists(stop):
                rs.check_dialogs()
                if not rs.alive():
                    rs.note("the runtime ended by itself")
                    break
                time.sleep(2)
            rs.note("hold ends (%s)" % ("stop file" if os.path.exists(stop) else "time"))
    finally:
        for p in (st_path, stop):
            try:
                os.remove(p)
            except OSError:
                pass
    print("\n".join(journal))
    return 0


def _need_hold() -> dict:
    st = active_hold()
    if not st:
        raise SystemExit("no hold process holds the runtime lock: start 'hold' first")
    return st


def cmd_exec(args) -> int:
    _need_hold()
    cap = CtCap()
    try:
        for c in args.calls:
            kind, _, rest = c.partition(" ")
            if kind == "C":
                win, _, stmt = rest.partition(" ")
                r = cap.cicode(stmt, int(win))
            elif kind == "R":
                r = cap.read(rest)
                if r.get("ok"):
                    r["quality"] = quality_name(quality_word(r))
            elif kind == "W":
                tag, _, val = rest.partition(" ")
                r = cap.write(tag, val)
            elif kind == "SHOT":
                win_no, _, path = rest.partition(" ")
                h = cap.cicode("WinGetWndHnd()", int(win_no))
                hwnd = int(float(h.get("value", "0") or 0)) & 0xFFFFFFFF
                dpi_aware()
                img = capture_client(hwnd)
                r = {
                    "ok": True,
                    "hwnd": hwnd,
                    "size": img.size,
                    "bytes": save_png(img, path),
                    "info": window_info(hwnd),
                }
            else:
                r = {"ok": False, "error": "unknown call kind %r" % kind}
            print(json.dumps({"call": c, "result": r}, ensure_ascii=False))
    finally:
        cap.close()
    return 0


def cmd_run(args) -> int:
    global CAPTURE_COUNT, CAPTURE_GAP
    CAPTURE_COUNT = max(1, int(args.captures))
    CAPTURE_GAP = max(0.05, float(args.capture_gap))
    plan = load_plan(args.plan)
    only = args.only or []
    out = os.path.abspath(args.out)
    journal: List[str] = []
    dpi_aware()
    if active_hold():
        log("using the running hold")
        cap = CtCap()
        try:
            res = run_batch(plan, out, only, None, cap, args.settle)
        finally:
            cap.close()
    else:
        with RuntimeSession(
            project_dir(plan.get("project", "ExampleSA")), journal, keep_overrides=args.keep_overrides
        ) as rs:
            res = run_batch(plan, out, only, rs, rs.cap, args.settle)
    print(json.dumps(res, indent=1))
    if journal:
        print("\n".join(journal))
    return 0


def cmd_stop(args) -> int:
    with open(os.path.join(state_dir(), "hold.stop"), "w") as fh:
        fh.write(_now())
    t0 = time.time()
    while active_hold() and time.time() - t0 < 120:
        time.sleep(2)
    print("hold ended" if not active_hold() else "hold still running")
    return 0


def verify_cases(root: str) -> dict:
    """Check every case under a pages folder."""
    from PIL import Image

    root = os.path.abspath(root)
    total, problems, notes, n_cases, n_frames = 0, [], [], 0, 0
    for case_json in sorted(glob.glob(os.path.join(root, "*", "*", "*", "case.json"))):
        d = os.path.dirname(case_json)
        rel = os.path.relpath(d, root).replace("\\", "/")
        try:
            with open(case_json, encoding="utf-8") as fh:
                doc = json.load(fh)
        except ValueError as e:
            problems.append("%s: %s" % (rel, e))
            continue
        n_cases += 1
        if doc.get("format") not in FORMATS:
            problems.append("%s: format %r" % (rel, doc.get("format")))
        v1 = doc.get("format") == FORMAT
        alarm_defs = doc.get("alarmDefs") or {}
        equip_defs = set(doc.get("equipmentDefs") or {})
        if v1:
            for k in ("alarmsRead", "alarmDefs", "equipmentRead", "equipmentDefs"):
                if not isinstance(doc.get(k), dict):
                    problems.append("%s: no %s" % (rel, k))
            for t in (doc.get("alarmsRead") or {}).get("notAdded") or []:
                problems.append("%s: the poll list refused the properties of alarm %s" % (rel, t))
        if (doc.get("scenario") or {}).get("format") != "ctsim-scenario/0":
            problems.append("%s: the scenario is not ctsim-scenario/0" % rel)
        client = (doc.get("window") or {}).get("client") or [0, 0, 0, 0]
        size = tuple(client[2:4])
        tags = set(doc.get("tags") or {})
        named = {"case.json"}
        last_at = -1.0
        for fr in doc.get("frames", []):
            n_frames += 1
            name = fr.get("name")
            for fname in frame_captures(fr)[0]:
                named.add(fname)
                p = os.path.join(d, fname)
                if not os.path.exists(p):
                    problems.append("%s: missing %s" % (rel, fname))
                    continue
                with Image.open(p) as im:
                    if im.size != size:
                        problems.append(
                            "%s: %s is %dx%d, the client area %dx%d"
                            % (rel, fname, im.size[0], im.size[1], size[0], size[1])
                        )
            snap = fr.get("snapshot") or {}
            if not snap:
                problems.append("%s: frame %s has no snapshot" % (rel, name))
            elif set(snap) != tags:
                problems.append(
                    "%s: frame %s: snapshot and tags differ (%d / %d)" % (rel, name, len(snap), len(tags))
                )
            for k, e in snap.items():
                if "error" not in e and not {"value", "text", "quality"} <= set(e):
                    problems.append("%s: frame %s: snapshot entry %s incomplete" % (rel, name, k))
                    break
            if v1:
                problems += _verify_frame_v1(rel, name, fr, alarm_defs, equip_defs)
                for t in fr.get("alarmsUnstable") or {}:
                    notes.append("%s %s: alarm %s changed during the capture" % (rel, name, t))
                for n in fr.get("equipmentChangedDuringCapture") or {}:
                    notes.append("%s %s: equipment %s changed during the capture" % (rel, name, n))
            at = fr.get("at")
            if not isinstance(at, (int, float)) or at < last_at:
                problems.append("%s: frame %s: 'at' %r out of order" % (rel, name, at))
            else:
                last_at = at
            for tag, ok in (fr.get("writesHeld") or {}).items():
                if not ok:
                    notes.append("%s %s: the write of %s did not hold" % (rel, name, tag))
            held = fr.get("qualityHeld")
            if held is None:
                # recorded before qualityHeld existed: judge from this frame's override calls
                held = {}
                for s in fr.get("steps") or []:
                    m = re.match(r'TagSetOverride(Bad|Good|Uncertain)\("(.*)"\)$', s.get("call", ""))
                    if m:
                        held[m.group(2)] = quality_held(snap.get(m.group(2)), m.group(1).lower())
            for tag, ok in held.items():
                if not ok:
                    notes.append(
                        "%s %s: the quality of %s did not take (snapshot %s)"
                        % (rel, name, tag, (snap.get(tag) or {}).get("quality"))
                    )
        for tag in doc.get("overriddenAtStart") or []:
            problems.append("%s: %s was in override mode when the case started" % (rel, tag))
        for r in doc.get("restore") or []:
            if not (r.get("result") or {}).get("ok"):
                problems.append("%s: the restore of %s failed" % (rel, r.get("set")))
        for f in os.listdir(d):
            total += os.path.getsize(os.path.join(d, f))
            if f not in named:
                problems.append("%s: %s is not named by the case" % (rel, f))
    return {"cases": n_cases, "frames": n_frames, "bytes": total, "problems": problems, "notes": notes}


def _verify_frame_v1(rel: str, name: str, fr: dict, alarm_defs: dict, equip_defs: set) -> List[str]:
    """The ctsim-pagecase/1 parts of a frame: ``alarms`` (alarms of alarmDefs, each read),
    ``equipment`` (every equipment of equipmentDefs, each field read)."""
    out = []
    alarms, equipment = fr.get("alarms"), fr.get("equipment")
    if not isinstance(alarms, dict):
        out.append("%s: frame %s has no alarms" % (rel, name))
    else:
        for t, e in alarms.items():
            if t not in alarm_defs:
                out.append("%s: frame %s: alarm %s is not in alarmDefs" % (rel, name, t))
            elif "error" in e:
                out.append("%s: frame %s: alarm %s could not be read (%s)" % (rel, name, t, e["error"]))
            elif not {"on", "ack", "disabled", "state"} <= set(e):
                out.append("%s: frame %s: alarm %s incomplete" % (rel, name, t))
    if not isinstance(equipment, dict):
        out.append("%s: frame %s has no equipment" % (rel, name))
    else:
        if set(equipment) != equip_defs:
            out.append(
                "%s: frame %s: equipment and equipmentDefs differ (%d / %d)"
                % (rel, name, len(equipment), len(equip_defs))
            )
        for n, e in equipment.items():
            for f in EQUIP_FIELDS:
                v = (e or {}).get(f)
                if not isinstance(v, str):
                    out.append("%s: frame %s: %s %s not read (%r)" % (rel, name, n, f, v))
    return out


def cmd_verify(args) -> int:
    """Check every case under a pages folder (see verify_cases)."""
    out = verify_cases(args.root)
    print(json.dumps(out, indent=1))
    return 1 if out["problems"] else 0


def _lock_held() -> bool:
    """Does some process hold C:\\ciprobe\\runtime.lock right now?"""
    import msvcrt

    try:
        fh = open(LOCK, "a+")
    except OSError:
        return False
    try:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return True
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    finally:
        fh.close()


def cmd_clean_overrides(args) -> int:
    """List (or with --apply remove) the Override elements the IO server's persistence caches
    hold for the given tags (variable tags or Equip.Item references; none = every tag that has
    one). Only with the runtime stopped and runtime.lock held by this command; only tags whose
    OverrideMode is 0. For residue a batch left behind."""
    from ctgedit.backend import runtime

    data = runtime_data_dir()
    if not data:
        raise SystemExit("no [CtEdit] Data folder in the active citect.ini")
    model = ProjectModel(project_dir(args.project)) if args.tags else None
    want = set()
    for t in args.tags or []:
        r = model.resolve(t)
        want.add((r[2] if r and r[2] else t).lower())
    journal: List[str] = []
    with RuntimeLock(journal):
        if runtime.running_processes():
            raise SystemExit("the runtime is running: stop it first (%s)" % runtime.running_processes())
        inv = override_inventory(data)
        plan = OrderedDict()
        for f, tags in sorted(inv.items()):
            sel = OrderedDict((t, m) for t, m in sorted(tags.items()) if not want or t.lower() in want)
            if sel:
                plan[f] = sel
        out = OrderedDict([("data", data), ("found", plan), ("removed", OrderedDict())])
        if args.apply:
            backup = os.path.join(state_dir(), "cachebak", time.strftime("%Y%m%d-%H%M%S"))
            for f, tags in plan.items():
                idle = [t for t, m in tags.items() if (m or "0").strip() == "0"]
                out["removed"][f] = remove_override_elements(os.path.join(data, f), idle, backup)
            out["backup"] = backup
            journal.append("%s clean-overrides removed %s" % (_now(), json.dumps(out["removed"])))
    with open(_journal_path(), "a", encoding="utf-8") as fh:
        fh.write("\n".join(journal) + "\n")
    print(json.dumps(out, indent=1))
    return 0


# --------------------------------------------------------------------------- comparing a simulator
#: the desktop of the recording sessions before cases recorded it (``window.screen``): 1920 x 1080
#: at 96 dpi. A window reaching past it (the 1920-wide pages at WinNewAt x 10: client x 11..1930)
#: is captured blank there: PrintWindow leaves the off-screen part of the runtime's client area
#: white
RECORDING_SCREEN = (0, 0, 1920, 1080)


def frame_captures(frame: dict) -> Tuple[List[str], bool]:
    """A frame's live captures, first one first."""
    names = [frame["capture"]] if frame.get("capture") else []
    more = frame.get("captures")
    if more:
        names += [n for n in more[1:] if n and n not in names]
    elif frame.get("capture2"):
        names.append(frame["capture2"])
    steady = len(names) == 1 and int(frame.get("capturesTaken") or 0) > 1
    return names, steady


def screen_rect() -> List[int]:
    """The virtual screen (every monitor) as [left, top, right, bottom] screen pixels."""
    x, y = _user32.GetSystemMetrics(76), _user32.GetSystemMetrics(77)
    return [x, y, x + _user32.GetSystemMetrics(78), y + _user32.GetSystemMetrics(79)]


def offscreen_masks(doc: dict) -> List[Tuple[int, int, int, int]]:
    """(x, y, w, h) client areas of a case's window that lay outside the screen when it was
    recorded (``window.screen``, else RECORDING_SCREEN): the capture is blank there, whatever
    the page draws, so a comparison leaves them out."""
    win = doc.get("window") or {}
    client = win.get("client") or [0, 0, 0, 0]
    cx, cy, w, h = (int(v) for v in client[:4])
    sx0, sy0, sx1, sy1 = (int(v) for v in (win.get("screen") or RECORDING_SCREEN))
    out = []
    if cx < sx0:
        out.append((0, 0, sx0 - cx, h))
    if cy < sy0:
        out.append((0, 0, w, sy0 - cy))
    if cx + w > sx1:
        x = max(0, sx1 - cx)
        out.append((x, 0, w - x, h))
    if cy + h > sy1:
        y = max(0, sy1 - cy)
        out.append((0, y, w, h - y))
    return out


def compare_images(live: List, sim, tolerance: int = 8, masks: List[Tuple[int, int, int, int]] = ()):
    """Per-pixel comparison of a simulator render with a live capture."""
    import numpy as np

    s = np.asarray(sim.convert("RGB"), dtype=np.int16)
    ok = None
    for im in live:
        a = np.asarray(im.convert("RGB"), dtype=np.int16)
        if a.shape != s.shape:
            raise ValueError("sizes differ: live %s, simulator %s" % (a.shape[1::-1], s.shape[1::-1]))
        m = np.abs(a - s).max(axis=2) <= tolerance
        ok = m if ok is None else (ok | m)
    use = np.ones(ok.shape, dtype=bool)
    for x, y, w, h in masks:
        use[max(0, y) : max(0, y + h), max(0, x) : max(0, x + w)] = False
    bad = (~ok) & use
    n = int(bad.sum())
    bbox = None
    if n:
        ys, xs = np.nonzero(bad)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    return n, int(use.sum()), bbox, bad


def diff_image(live, bad):
    """The live capture in grey, the differing pixels in red."""
    import numpy as np
    from PIL import Image

    g = np.asarray(live.convert("L"), dtype=np.uint8)
    rgb = np.stack([g // 2 + 96] * 3, axis=2)
    rgb[bad] = (255, 0, 0)
    return Image.fromarray(rgb.astype(np.uint8), "RGB")


def cmd_compare(args) -> int:
    """Compare a simulator render with a recorded frame (see the README of the pages)."""
    from PIL import Image

    case_dir = os.path.abspath(args.case)
    with open(os.path.join(case_dir, "case.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    frame = next((f for f in doc["frames"] if f["name"] == args.frame), None)
    if frame is None:
        raise SystemExit("no frame %r in %s" % (args.frame, case_dir))
    names = frame_captures(frame)[0]
    live = [Image.open(os.path.join(case_dir, n)) for n in names[: 1 if args.first_only else None]]
    sim = Image.open(args.render)
    w, h = doc["window"]["client"][2:]
    if sim.size != (w, h):
        x, y = (int(v) for v in (args.offset or "0,0").split(","))
        sim = sim.crop((x, y, x + w, y + h))
    masks = [tuple(int(v) for v in m.split(",")) for m in (args.mask or [])]
    masks += offscreen_masks(doc)
    n, total, bbox, bad = compare_images(live, sim, args.tolerance, masks)
    ratio = n / float(total or 1)
    if args.diff:
        diff_image(live[0], bad).save(args.diff, "PNG", optimize=True)
    out = OrderedDict(
        [
            ("case", args.case),
            ("frame", args.frame),
            ("size", [w, h]),
            ("tolerance", args.tolerance),
            ("differing", n),
            ("compared", total),
            ("ratio", round(ratio, 6)),
            ("bbox", bbox),
            ("pass", ratio <= args.max_ratio),
        ]
    )
    print(json.dumps(out))
    return 0 if out["pass"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", default="ExampleSA")
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--ctsim")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("classes")
    p.add_argument("pages", nargs="*")
    p = sub.add_parser("tags")
    p.add_argument("page")
    p.add_argument("--assoc", action="append")
    p = sub.add_parser("hold")
    p.add_argument("--minutes", type=float, default=90.0)
    p.add_argument(
        "--keep-overrides",
        action="store_true",
        help="leave the Override elements the batch created in the IO server caches",
    )
    sub.add_parser("stop")
    p = sub.add_parser("exec")
    p.add_argument(
        "calls", nargs="+", help="'C <window> <call>', 'R <tag>', 'W <tag> <value>', 'SHOT <window> <png>'"
    )
    p = sub.add_parser("run")
    p.add_argument("--out", required=True)
    p.add_argument("--only", action="append")
    p.add_argument("--settle", type=float, default=2.5)
    p.add_argument(
        "--captures",
        type=int,
        default=CAPTURE_COUNT,
        help="captures per frame (default %(default)s; identical ones are kept once)",
    )
    p.add_argument(
        "--capture-gap",
        type=float,
        default=CAPTURE_GAP,
        help="seconds between a frame's captures (default %(default)s)",
    )
    p.add_argument(
        "--keep-overrides",
        action="store_true",
        help="leave the Override elements the batch created in the IO server caches",
    )
    p = sub.add_parser("clean-overrides")
    p.add_argument("tags", nargs="*", help="variable tags or Equip.Item references (none = all)")
    p.add_argument("--apply", action="store_true", help="remove them (else only list)")
    p = sub.add_parser("compare")
    p.add_argument("case", help="a case folder (holds case.json)")
    p.add_argument("frame")
    p.add_argument("render", help="the simulator's PNG of the window (or a bigger image, see --offset)")
    p.add_argument("--offset", help="x,y of the page area in the render when it is bigger")
    p.add_argument("--tolerance", type=int, default=8)
    p.add_argument("--max-ratio", type=float, default=0.0)
    p.add_argument("--mask", action="append", help="x,y,w,h of a page area to leave out")
    p.add_argument("--diff", help="write the diff image here")
    p.add_argument("--first-only", action="store_true", help="compare with the first capture only")
    p = sub.add_parser("verify")
    p.add_argument("root")
    args = ap.parse_args(argv)
    return {
        "classes": cmd_classes,
        "tags": cmd_tags,
        "hold": cmd_hold,
        "stop": cmd_stop,
        "exec": cmd_exec,
        "run": cmd_run,
        "verify": cmd_verify,
        "compare": cmd_compare,
        "clean-overrides": cmd_clean_overrides,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
