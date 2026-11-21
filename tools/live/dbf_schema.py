"""Write ctgedit/format/dbf_schema.json from the install's citect.frm and tables.

python tools/live/dbf_schema.py                write the schema again
python tools/live/dbf_schema.py --check        compare with the install
python tools/live/dbf_schema.py --frm F --user U [--out FILE]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from ctgedit.format import dbfschema as S  # noqa: E402

#: the projects AVEVA ships with the install (the only ones read: never a user's)
SHIPPED = (
    "ExampleSA",
    "Example",
    "Include",
    "System",
    "SA_Include",
    "SA_Library",
    "SA_Controls",
    "Library_Controls",
    "Library_Equipment",
    "IndustrialGraphics_Include",
    "SxW_Style_Include",
    "Tab_Style_Include",
)
ABOUT = (
    "The project tables' layouts (tools/live/dbf_schema.py writes this file from the "
    "install's Bin\\citect.frm and the shipped projects' tables; docs/CTG_FORMAT.md, "
    "'Project tables'). Per table: its fields in the order a new table "
    "has them, [NAME, type, size, decimals]; 'index': the field its <table>.NDX is "
    "keyed on (the form's INDEX / WRITEINDEXED KEY); 'retired': fields older tables "
    "still hold that the form no longer lists."
)


def _install_paths():
    from ctgedit.infra import install

    b, user = install.bin_dir(), install.user_dir()
    frm = os.path.join(b, "citect.frm") if b else None
    return frm, user


def _dbfs(folder: str, ext_want: str = ".dbf"):
    """{lower table name: path} of a project folder's tables (``.ndx``: its indexes)."""
    out = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for f in names:
        stem, ext = os.path.splitext(f)
        if ext.lower() == ext_want:
            out.setdefault(stem.lower(), os.path.join(folder, f))
    return out


def _language_files(folder: str, tables: dict):
    """The language tables a project's lang.dbf names (SRC_FILE)."""
    from ctgedit.format.projects import read_dbf_rows

    p = tables.get("lang")
    if not p:
        return []
    out = []
    for r in read_dbf_rows(p):
        src = (r.get("SRC_FILE") or "").strip()
        stem = os.path.splitext(os.path.basename(src))[0].lower()
        if stem and stem in tables:
            out.append(tables[stem])
    return out


def build(frm: str, user: str, notes=None):
    """(schema dict, notes): the schema of the install at ``frm`` / ``user``."""
    notes = [] if notes is None else notes
    forms = S.read_frm(frm)
    by_table = S.forms_by_table(forms)
    projects = [(p, _dbfs(os.path.join(user, p))) for p in SHIPPED if os.path.isdir(os.path.join(user, p))]
    indexes = {p: _dbfs(os.path.join(user, p), ".ndx") for p, _t in projects}
    if not projects:
        raise SystemExit("No shipped project was found in %s." % user)
    out = {}
    retired = {}
    for key in sorted(by_table):
        form = by_table[key]
        want = S.form_fields(form)
        ent = {"form": form.menu, "fields": [list(f) for f in want]}
        index = S.form_index(form)
        if index:
            ent["index"] = index
        elif form.indexed:
            notes.append("%s: the form's KEY %s is not one of its fields" % (key, form.index_key))
        names = {n.upper() for n, *_r in want}
        olds = collections.OrderedDict()
        for proj, tables in projects:
            p = tables.get(key)
            if not p:
                continue
            if index:
                width = next(w for n, _t, w, _d in want if n == index)
                notes += S.index_problems(p, index, width, "%s\\%s" % (proj, os.path.basename(p)))
            have = S.read_fields(p)
            for line in S.compare(have, want, "%s\\%s" % (proj, os.path.basename(p)), extra=False):
                notes.append(line)
            for n, t, w, d in have:
                if n.upper() not in names:
                    olds.setdefault(n.upper(), [n.upper(), t, w, d])
        if olds:
            retired[key] = list(olds.values())
            notes.append("%s: the shipped tables also hold %s (not in the form)" % (key, ", ".join(olds)))
        out[key] = ent
    # MASTER.DBF: the install's own
    mp = os.path.join(user, "MASTER.DBF")
    if not os.path.isfile(mp):
        raise SystemExit("No MASTER.DBF in %s." % user)
    out[S.MASTER] = {"file": "User\\MASTER.DBF", "fields": [list(f) for f in S.read_fields(mp)]}
    # the language tables: one layout across the shipped projects
    layouts = collections.Counter()
    keys = collections.Counter()
    for proj, tables in projects:
        for p in _language_files(os.path.join(user, proj), tables):
            layouts[tuple(S.read_fields(p))] += 1
            stem = os.path.splitext(os.path.basename(p))[0].lower()
            keys[S.ndx_key(indexes[proj].get(stem, ""))] += 1
    if not layouts:
        raise SystemExit("No shipped project names a language table in its lang.dbf.")
    if len(layouts) > 1:
        notes.append("the language tables have %d layouts: %s" % (len(layouts), dict(layouts)))
    lang = layouts.most_common(1)[0][0]
    out[S.LANGUAGE] = {
        "file": "<project>\\<language>.dbf (lang.dbf SRC_FILE)",
        "fields": [list(f) for f in lang],
    }
    if len(keys) > 1:
        notes.append("the language tables' indexes differ: %s" % dict(keys))
    nk = keys.most_common(1)[0][0]
    if nk is not None:
        field = next((n for n, _t, w, _d in lang if n.upper() == nk[0] and w == nk[1]), None)
        if field:
            out[S.LANGUAGE]["index"] = field
        else:
            notes.append("the language tables' index %s (key %d) is not one of their fields" % nk)
    return {"about": ABOUT, "tables": out, "retired": retired}, notes


def dumps(schema: dict) -> str:
    """The JSON text: a field per line (a readable diff)."""
    lines = ["{", '  "about": %s,' % json.dumps(schema["about"]), '  "tables": {']
    keys = sorted(schema["tables"])
    for i, k in enumerate(keys):
        ent = schema["tables"][k]
        head = ", ".join('"%s": %s' % (a, json.dumps(ent[a])) for a in ("form", "file", "index") if a in ent)
        lines.append('    %s: {%s, "fields": [' % (json.dumps(k), head))
        fl = ent["fields"]
        for j, f in enumerate(fl):
            lines.append("      %s%s" % (json.dumps(f), "," if j < len(fl) - 1 else ""))
        lines.append("    ]}%s" % ("," if i < len(keys) - 1 else ""))
    lines.append("  },")
    lines.append('  "retired": {')
    rk = sorted(schema["retired"])
    for i, k in enumerate(rk):
        lines.append(
            "    %s: %s%s" % (json.dumps(k), json.dumps(schema["retired"][k]), "," if i < len(rk) - 1 else "")
        )
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def check(committed: dict, fresh: dict) -> list:
    """The differences of the committed schema from a fresh one, one line each."""
    out = []
    a, b = committed.get("tables", {}), fresh.get("tables", {})
    for k in sorted(set(a) | set(b)):
        if k not in b:
            out.append("%s: in the committed schema, not in the install" % k)
        elif k not in a:
            out.append("%s: in the install, not in the committed schema" % k)
        else:
            fa = [tuple(f) for f in a[k]["fields"]]
            fb = [tuple(f) for f in b[k]["fields"]]
            if fa != fb:
                out += S.compare(fa, fb, k, order=True) or ["%s: the fields differ" % k]
            ia, ib = a[k].get("index", ""), b[k].get("index", "")
            if ia != ib:
                out.append("%s: the index is on %s, not %s" % (k, ia or "no field", ib or "no field"))
    if committed.get("retired", {}) != fresh.get("retired", {}):
        out.append("the retired fields differ")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--check", action="store_true", help="compare the committed schema with the install; write nothing"
    )
    ap.add_argument("--frm", help="the citect.frm to read (default: the install's Bin)")
    ap.add_argument("--user", help="the User folder (default: the install's)")
    ap.add_argument("--out", default=S.SCHEMA_PATH, help="the schema file")
    ap.add_argument("--quiet", action="store_true", help="no notes")
    a = ap.parse_args(argv)
    frm, user = a.frm, a.user
    if not (frm and user):
        f2, u2 = _install_paths()
        frm, user = frm or f2, user or u2
    if not frm or not os.path.isfile(frm):
        print("No citect.frm was found (%s)." % frm)
        return 2
    if not user or not os.path.isdir(user):
        print("No User folder was found (%s)." % user)
        return 2
    schema, notes = build(frm, user)
    if not a.quiet:
        for n in notes:
            print("note: " + n)
    if a.check:
        try:
            with open(a.out, encoding="utf-8") as fh:
                committed = json.load(fh)
        except (OSError, ValueError) as e:
            print("The committed schema cannot be read: %s" % e)
            return 1
        diff = check(committed, schema)
        for d in diff:
            print(d)
        print(
            "The schema matches the install."
            if not diff
            else "%d differences: run tools/live/dbf_schema.py to write it again." % len(diff)
        )
        return 1 if diff else 0
    text = dumps(schema)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("Wrote %s: %d tables." % (a.out, len(schema["tables"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
