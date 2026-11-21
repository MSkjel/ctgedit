"""Acceptance checks of the ctsim-page/0 export (ctgedit/sim/pagemodel.py). Read only.

    python tools/sim_page_model_check.py rows [<project dir> ...] [--json <out>] [--diff N]
    python tools/sim_page_model_check.py roundtrip [<page.ctg> ...] [--pages N] [--seed S]

rows: for every page of the projects (default Example + ExampleSA) that is compiled (a
<page>.RDB next to it), the exported ``tables`` rows normalised the way the DBF stores them
(Dbf.encode -> decode: field widths, trailing blanks) against the page's live rows in the
project's pg*.DBF tables (+ activex.DBF), as multisets per table. Prints the agreement and the
first differing rows.

roundtrip: export the pages (default a seeded sample of 20 compiled Example / ExampleSA pages,
always including Mixer, Meters, Overview_L2 and Others), dump to JSON, re-read it and compare
with the document (pagemodel.check_model).
"""

import collections
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("CTG_NO_CTF_PRECHECK", "1")

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
DEFAULT_PROJECTS = [os.path.join(R2, "Example"), os.path.join(R2, "ExampleSA")]
ALWAYS = ("Mixer", "Meters", "Overview_L2", "Others")


def compiled_pages(project_dir):
    """(page name, .ctg path) of every page with a <page>.RDB, sorted by name."""
    names = os.listdir(project_dir)
    rdb = {os.path.splitext(f)[0].lower() for f in names if f.lower().endswith(".rdb")}
    out = []
    for f in sorted(names, key=str.lower):
        stem, ext = os.path.splitext(f)
        if ext.lower() == ".ctg" and stem.lower() in rdb:
            out.append((stem, os.path.join(project_dir, f)))
    return out


def light_document(path):
    """An EditableDocument holding the parsed page WITHOUT the editor's derive (the export
    reads only the records, the project and the attributes)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg

    doc = EditableDocument(path=path)
    doc.parsed = parse_ctg(path)
    return doc


class _Tables:
    """The project's page tables, opened once (read only)."""

    def __init__(self, project_dir):
        from ctgedit.format.dbf import Dbf
        from ctgedit.io import pgregister as PR

        self.dir = project_dir
        self.dbf = {}
        self.ndx = {}
        for t in PR.PAGE_TABLES + ("activex",):
            p = PR._find_file(project_dir, t + ".DBF")
            if p is None:
                continue
            self.dbf[t] = Dbf(p, writable=False)
            self.ndx[t] = PR._open_ndx(project_dir, t, writable=False)

    def rows(self, table, page):
        from ctgedit.io import pgregister as PR

        d = self.dbf.get(table)
        if d is None or "PAGE" not in d.fmap:
            return None
        return [
            d.decode(r)
            for _i, r in sorted(PR._page_records(d, self.ndx.get(table), "PAGE", page))
            if r[:1] != b"*"
        ]

    def norm(self, table, row):
        d = self.dbf[table]
        return d.decode(d.encode(row))

    def close(self):
        for x in list(self.dbf.values()) + [n for n in self.ndx.values() if n is not None]:
            x.close()


def _key(row):
    """A row as a comparable value; PAGE without case (page names are case-insensitive, and
    pages.DBF and the pg rows can spell one differently)."""
    return tuple(sorted((k, v.lower() if k == "PAGE" else v) for k, v in row.items()))


def _changed_values(norm, row):
    """Exported values the DBF would store differently (cut at the field width, trailing
    blanks): what the compiler reads is the stored form."""
    up = {k.upper(): v for k, v in row.items()}
    return [k for k, v in norm.items() if (up.get(k.upper()) or "") != v]


def rows_check(projects, diff_n=5, out_json=None):
    from ctgedit.sim import pagemodel

    tot = collections.Counter()
    per_table = collections.defaultdict(collections.Counter)
    diffs = []
    pages_bad = []
    dbf_changed = []
    t0 = time.time()
    for proj in projects:
        tabs = _Tables(proj)
        try:
            for name, path in compiled_pages(proj):
                tot["pages"] += 1
                try:
                    doc = light_document(path)
                    model_rows = pagemodel.page_tables(doc, pagemodel._page_name(doc))
                    page = pagemodel._page_name(doc)
                except Exception as e:  # noqa: BLE001
                    tot["pages_failed"] += 1
                    pages_bad.append((os.path.basename(proj), name, "%s: %s" % (type(e).__name__, e)))
                    continue
                page_ok = True
                for table, rows in model_rows.items():
                    have = tabs.rows(table, page)
                    if have is None:
                        if rows:
                            per_table[table]["no_table"] += len(rows)
                        continue
                    if table == "activex":
                        # the table only grows (rows of removed controls are kept): compare
                        # the rows of the controls on the page
                        ans = {r["AN"] for r in rows}
                        have = [r for r in have if r.get("AN") in ans]
                    normed = [tabs.norm(table, r) for r in rows]
                    for nr, r in zip(normed, rows):
                        up = {k.upper(): v for k, v in r.items()}
                        for f in _changed_values(nr, r):
                            raw = up.get(f.upper()) or ""
                            # trailing blanks / CR LF only (the DBF reader right-strips) or a
                            # value cut at its field width
                            kind = "trailing_blanks" if raw.rstrip() == nr[f] else "cut_at_width"
                            per_table[table]["dbf_" + kind] += 1
                            if kind == "cut_at_width" and len(dbf_changed) < 20:
                                dbf_changed.append(
                                    (os.path.basename(proj), name, table, r.get("AN"), f, r.get(f))
                                )
                    want = collections.Counter(_key(r) for r in normed)
                    got = collections.Counter(_key(r) for r in have)
                    same = sum((want & got).values())
                    extra = want - got
                    missing = got - want
                    per_table[table]["rows"] += sum(got.values())
                    per_table[table]["exported"] += sum(want.values())
                    per_table[table]["equal"] += same
                    per_table[table]["exported_not_stored"] += sum(extra.values())
                    per_table[table]["stored_not_exported"] += sum(missing.values())
                    if extra or missing:
                        page_ok = False
                        if len(diffs) < diff_n:
                            diffs.append(
                                {
                                    "project": os.path.basename(proj),
                                    "page": name,
                                    "table": table,
                                    "exported_not_stored": [dict(k) for k in list(extra)[:2]],
                                    "stored_not_exported": [dict(k) for k in list(missing)[:2]],
                                }
                            )
                tot["pages_equal" if page_ok else "pages_differ"] += 1
        finally:
            tabs.close()
    res = {
        "totals": dict(tot),
        "tables": {t: dict(c) for t, c in sorted(per_table.items())},
        "failed": pages_bad,
        "dbf_changed": dbf_changed,
        "seconds": round(time.time() - t0, 1),
        "diffs": diffs,
    }
    if out_json:
        with open(out_json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1)
    return res


def roundtrip_check(paths):
    from ctgedit.editing import EditableDocument
    from ctgedit.sim import pagemodel

    out = {"pages": 0, "objects": 0, "bad": {}, "seconds": 0.0, "export_s": {}}
    t0 = time.time()
    for p in paths:
        doc = EditableDocument.open(p)
        t1 = time.perf_counter()
        model = doc.export_page_model()
        out["export_s"][os.path.basename(p)] = round(time.perf_counter() - t1, 3)
        text = pagemodel.dumps(model)
        bad = pagemodel.check_model(pagemodel.loads(text), doc)
        out["pages"] += 1
        out["objects"] += len(model["objects"])
        if bad:
            out["bad"][p] = bad[:20]
    out["seconds"] = round(time.time() - t0, 1)
    return out


def sample_pages(n, seed):
    pages = []
    for proj in DEFAULT_PROJECTS:
        pages += [p for _n, p in compiled_pages(proj)]
    fixed = [p for p in pages if os.path.splitext(os.path.basename(p))[0] in ALWAYS]
    rest = [p for p in pages if p not in fixed]
    random.Random(seed).shuffle(rest)
    return fixed + rest[: max(0, n - len(fixed))]


def main(argv):
    args = list(argv)
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    mode = args.pop(0)

    def opt(name, default=None, conv=str):
        if name in args:
            k = args.index(name)
            v = conv(args[k + 1])
            del args[k : k + 2]
            return v
        return default

    if mode == "rows":
        out = opt("--json")
        dn = opt("--diff", 5, int)
        res = rows_check(args or DEFAULT_PROJECTS, dn, out)
        print(json.dumps(res, indent=1))
        return 0 if not res["failed"] else 1
    if mode == "roundtrip":
        n = opt("--pages", 20, int)
        seed = opt("--seed", 1, int)
        res = roundtrip_check(args or sample_pages(n, seed))
        print(json.dumps(res, indent=1))
        return 0 if not res["bad"] else 1
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
