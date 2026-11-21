"""A queryable index of every record of every page (and library element) in
the live corpus.

It builds a SQLite index once (in parallel) and afterwards re-parses only the
files that changed (mtime + size):

    python tools/corpus_index.py build [--workers 4] [--libs] [--root <User dir>]
    python tools/corpus_index.py query "SELECT tc, COUNT(*) FROM records GROUP BY tc"
    python tools/corpus_index.py sql            # print the schema

Tables:
  files(id, path, project, name, ext, mtime, size, objects, error)
      path = the file, or '<file>#<element>' for a library element (--libs)
  records(file_id, idx, parent, depth, tc, dwflags, child_count, handle,
          l, t, r, b, body_len, style_flags, nodes, keys, props)
      style_flags = the 6 flag bytes of the exact style block (hex, NULL
      without one); nodes = the vertex count of a node array; keys = the
      record's bag keys as ' K1 K2 ... ' (so ``keys LIKE '% COL_MAXON %'``);
      props = the bag as zlib-compressed JSON, read with the SQL function
      ``prop(props, 'KEY')`` (registered by :func:`connect`).
CtDraw's property-sheet UI state keys ('!*...') are not indexed.
The database lives in <repo>/tools/_corpus/corpus.sqlite (git-ignored). Pure
stdlib + ctgedit.format; no Qt."""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
DB = os.environ.get("CTG_CORPUS_DB") or os.path.join(HERE, "_corpus", "corpus.sqlite")
DEFAULT_ROOT = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT UNIQUE, project TEXT,
    name TEXT, ext TEXT, mtime REAL, size INTEGER, objects INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS records(file_id INTEGER, idx INTEGER, parent INTEGER, depth INTEGER,
    tc INTEGER, dwflags INTEGER, child_count INTEGER, handle INTEGER,
    l INTEGER, t INTEGER, r INTEGER, b INTEGER, body_len INTEGER, style_flags TEXT,
    nodes INTEGER, keys TEXT, props BLOB);
CREATE INDEX IF NOT EXISTS rec_tc ON records(tc);
CREATE INDEX IF NOT EXISTS rec_file ON records(file_id, idx);
"""


def _records_of(objs):
    """records rows of one page's object list."""
    from ctgedit.format import geometry as G
    from ctgedit.format.placement import forest_parents

    try:
        par = forest_parents(objs)
    except Exception:  # noqa: BLE001
        par = [None] * len(objs)
    depth = []
    recs = []
    for i, po in enumerate(objs):
        p = par[i]
        depth.append(0 if p is None else depth[p] + 1)
        try:
            hd = G.parse_geom_header(po.body_raw)
            l, t, r, b = hd.rect1 if hd else (None,) * 4
        except Exception:  # noqa: BLE001
            l = t = r = b = None
        try:
            cb = G.find_colour_block_exact(po)
            sf = bytes(cb.flags[:6]).hex() if cb is not None else None
        except Exception:  # noqa: BLE001
            sf = None
        try:
            na = G.find_node_array(po)
            nodes = na.count if na is not None else None
        except Exception:  # noqa: BLE001
            nodes = None
        try:
            bag = {k: v for k, v in po.raw_properties().items() if not k.startswith("!*")}
        except Exception:  # noqa: BLE001
            bag = {}
        keys = (" " + " ".join(bag) + " ") if bag else None
        blob = zlib.compress(json.dumps(bag, separators=(",", ":")).encode("utf-8"), 6) if bag else None
        recs.append(
            (
                i,
                p,
                depth[i],
                po.type_code,
                po.subtype_size,
                po.class_flag,
                G.get_handle(po),
                l,
                t,
                r,
                b,
                len(po.body_raw),
                sf,
                nodes,
                keys,
                blob,
            )
        )
    return recs


def _scan(path: str):
    """Parse one file (in a worker process) -> [(key, records, error)]: one
    entry for a page, one per element ('<path>#<element>') for a library."""
    if path.lower().endswith(".ctg"):
        from ctgedit.format.page import parse_ctg

        try:
            pp = parse_ctg(path)
            if pp.page is None:
                return [(path, None, "no page block")]
            return [(path, _records_of(pp.page.objects), None)]
        except Exception as e:  # noqa: BLE001
            return [(path, None, "%s: %s" % (type(e).__name__, e))]
    from ctgedit.format.symbols import SymbolLibrary

    try:
        lib = SymbolLibrary.open(path)
        names = lib.element_names()
    except Exception as e:  # noqa: BLE001
        return [(path, None, "%s: %s" % (type(e).__name__, e))]
    out = []
    for name in names:
        pb = lib.element(name)
        if pb is None:
            out.append((path + "#" + name, None, "element does not parse"))
        else:
            out.append((path + "#" + name, _records_of(pb.objects), None))
    return out


def _prop(blob, key):
    if blob is None:
        return None
    try:
        return json.loads(zlib.decompress(blob)).get(key)
    except Exception:  # noqa: BLE001
        return None


def connect(db: str = DB) -> sqlite3.Connection:
    """A connection with the ``prop(props, key)`` SQL function registered."""
    con = sqlite3.connect(db)
    con.create_function("prop", 2, _prop, deterministic=True)
    return con


def props_of(blob) -> dict:
    """The full bag dict of a ``records.props`` blob."""
    return json.loads(zlib.decompress(blob)) if blob else {}


def build(root: str = DEFAULT_ROOT, workers: int = 4, libs: bool = False) -> None:
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = connect()
    con.executescript(SCHEMA)
    pats = ["*.ctg"] + (["*.ctm", "*.ctl", "*.ctt"] if libs else [])
    paths = sorted(p for pat in pats for p in glob.glob(os.path.join(root, "*", pat)))
    known = {}
    for key, mtime, size, fid in con.execute("SELECT path, mtime, size, id FROM files"):
        known.setdefault(key.split("#", 1)[0], [mtime, size, []])[2].append(fid)
    todo = []
    for p in paths:
        st = os.stat(p)
        k = known.get(p)
        if k is None or k[0] != st.st_mtime or k[1] != st.st_size:
            todo.append(p)
    pset = set(paths)
    gone = [p for p in known if p not in pset]
    for p in gone + [p for p in todo if p in known]:
        for fid in known[p][2]:
            con.execute("DELETE FROM records WHERE file_id=?", (fid,))
            con.execute("DELETE FROM files WHERE id=?", (fid,))
    con.commit()
    t0 = time.time()
    print("%d files, %d to (re)index, %d removed" % (len(paths), len(todo), len(gone)))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for n, (path, entries) in enumerate(zip(todo, ex.map(_scan, todo, chunksize=8)), 1):
            st = os.stat(path)
            project = os.path.basename(os.path.dirname(path))
            stem, ext = os.path.splitext(os.path.basename(path))
            for key, recs, err in entries:
                name = stem if key == path else stem + "." + key.split("#", 1)[1]
                cur = con.execute(
                    "INSERT INTO files(path, project, name, ext, mtime, size, objects, error) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        key,
                        project,
                        name,
                        ext.lower(),
                        st.st_mtime,
                        st.st_size,
                        len(recs) if recs is not None else None,
                        err,
                    ),
                )
                if recs:
                    fid = cur.lastrowid
                    con.executemany(
                        "INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [(fid,) + r for r in recs],
                    )
            if n % 300 == 0:
                con.commit()
                print("  %d / %d  (%.0fs)" % (n, len(todo), time.time() - t0))
    con.commit()
    n_rec = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    con.execute("VACUUM") if gone else None
    con.close()
    print("indexed in %.0fs: %d records, %.0f MB" % (time.time() - t0, n_rec, os.path.getsize(DB) / 1e6))


def query(sql: str, params=()):
    con = connect()
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, cur.fetchall()
    finally:
        con.close()


def main(argv):
    if not argv or argv[0] not in ("build", "query", "sql"):
        print(__doc__)
        return 2
    if argv[0] == "build":
        workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 4
        root = argv[argv.index("--root") + 1] if "--root" in argv else DEFAULT_ROOT
        build(root, workers, libs="--libs" in argv)
        return 0
    if argv[0] == "sql":
        print(SCHEMA)
        return 0
    cols, rows = query(argv[1])
    print("\t".join(cols))
    for r in rows:
        print("\t".join("" if v is None else str(v) for v in r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
