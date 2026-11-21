"""Resolve multi-language labels."""

from __future__ import annotations

import os
import struct
from ..format.textcodec import CODEC


# Each <Language>.DBF stores its LOCAL strings in the Windows ANSI code page of
# THAT language (the Western languages are cp1252).
# NATIVE stays on the page codec: it is the key matched against the page's
# ``@(...)`` strings. Keyed on the LANG.DBF language name's first word.
_LANG_CODEPAGE = {
    "japanese": "cp932",
    "korean": "cp949",
    "thai": "cp874",
    "greek": "cp1253",
    "turkish": "cp1254",
    "hebrew": "cp1255",
    "arabic": "cp1256",
    "farsi": "cp1256",
    "persian": "cp1256",
    "urdu": "cp1256",
    "estonian": "cp1257",
    "latvian": "cp1257",
    "lithuanian": "cp1257",
    "vietnamese": "cp1258",
    **{
        k: "cp1251"
        for k in ("russian", "ukrainian", "bulgarian", "belarusian", "serbian", "macedonian", "kazakh")
    },
    **{
        k: "cp1250"
        for k in (
            "polish",
            "czech",
            "slovak",
            "hungarian",
            "hungary",
            "slovenian",
            "croatian",
            "romanian",
            "albanian",
        )
    },
}


def language_codepage(name: str) -> str:
    """Python codec for the LOCAL column of language ``name`` (a LANG.DBF
    'Language' value such as 'Chinese(Traditional, Taiwan)')."""
    low = (name or "").strip().lower()
    first = low.split("(")[0].strip().split(" ")[0] if low else ""
    if first == "chinese":
        return "cp950" if ("traditional" in low or "taiwan" in low or "hong kong" in low) else "gbk"
    return _LANG_CODEPAGE.get(first, CODEC)


def _read_dbf(path: str, encodings: dict | None = None):
    """(field_names, rows) of a dBASE III table."""
    with open(path, "rb") as fh:
        b = fh.read()
    if len(b) < 32:
        return [], []
    nrec, hdr, rln = struct.unpack_from("<IHH", b, 4)
    fields = []
    off = 32
    while off < len(b) and b[off] != 0x0D:
        name = b[off : off + 11].split(b"\x00")[0].decode(CODEC)
        flen = b[off + 16]
        fields.append((name, flen))
        off += 32
    encodings = {k.upper(): v for k, v in (encodings or {}).items()}
    rows = []
    for i in range(nrec):
        base = hdr + i * rln
        rec = b[base : base + rln]
        if len(rec) < rln or rec[:1] == b"*":  # deleted / truncated
            continue
        o = 1
        row = {}
        for name, flen in fields:
            codec = encodings.get(name.upper(), CODEC)
            row[name] = rec[o : o + flen].decode(codec, "replace").rstrip()
            o += flen
        rows.append(row)
    return [f[0] for f in fields], rows


#: a folder's file names by their lower case (the first listed), kept while the folder's
#: mtime is the same
_LISTING_CACHE: dict = {}


def _find_file_ci(d: str, name: str):
    """Case-insensitive file lookup in ``d`` (DBF names vary in case)."""
    target = name.lower()
    try:
        stamp = os.stat(d).st_mtime_ns
        hit = _LISTING_CACHE.get(d)
        if hit is None or hit[0] != stamp:
            names: dict = {}
            for f in os.listdir(d):
                names.setdefault(f.lower(), f)
            hit = _LISTING_CACHE[d] = (stamp, names)
    except OSError:
        return None
    f = hit[1].get(target)
    return os.path.join(d, f) if f is not None else None


def _project_dir_with_lang(page_path: str, extra_dirs=()):
    """The project dir that holds ``LANG.DBF`` -- the page dir, then any
    ``CTG_LIB_PATH`` entry (set during temp re-renders so the REAL project dir
    is reachable), then explicit extras."""
    dirs = [os.path.dirname(os.path.abspath(page_path))]
    libp = os.environ.get("CTG_LIB_PATH")
    if libp:
        dirs += [d for d in libp.split(os.pathsep) if d]
    dirs += list(extra_dirs)
    for d in dirs:
        if d and _find_file_ci(d, "LANG.DBF"):
            return d
    return None


class LanguageDB:
    """The project's language tables, loaded lazily + cached per project dir."""

    _cache: dict = {}

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self._names = self._load_names()
        self._tables: dict = {}  # language name -> {native: local}
        #: LANG.DBF and the language tables as read: a changed one gives a new db
        self.stamp = self._files_stamp()

    # ---- construction ----
    @classmethod
    def for_page(cls, page_path: str, extra_dirs=()):
        """The LanguageDB for the project that owns ``page_path``, or None. The db held
        for the project is kept while LANG.DBF and its language tables are as it read them
        (:meth:`_files_stamp`): a language or translation added in Studio gives a new one
        (the reader's derive context compares its identity: the labels are drawn again)."""
        d = _project_dir_with_lang(page_path, extra_dirs)
        if d is None:
            return None
        key = os.path.normcase(d)
        db = cls._cache.get(key)
        if db is not None and db.stamp != db._files_stamp():
            cls._cache.pop(key, None)
            db = None
        if db is None:
            # (built meanwhile on another thread: every caller shares the first one -- the
            # reader's derive context compares its identity)
            db = cls._cache.setdefault(key, cls(d))
        return db

    def _files_stamp(self) -> tuple:
        """((path, mtime_ns, size), ...) of LANG.DBF and each language's table (the
        listing cache finds them by the folder's mtime: a new table is seen too)."""
        files = [_find_file_ci(self.project_dir, "LANG.DBF")]
        files += [self._file_for(n) for n in getattr(self, "_names", ())]
        out = []
        for f in files:
            if not f:
                continue
            try:
                st = os.stat(f)
            except OSError:
                out.append((f, None, None))
                continue
            out.append((f, st.st_mtime_ns, st.st_size))
        return tuple(out)

    @classmethod
    def clear_cache(cls):
        cls._cache.clear()

    def _load_names(self):
        f = _find_file_ci(self.project_dir, "LANG.DBF")
        if not f:
            return []
        try:
            _fields, rows = _read_dbf(f)
        except Exception:  # noqa: BLE001
            return []
        out = []
        self._src = {}
        for r in rows:
            r = {k.upper(): v for k, v in r.items()}
            name = (r.get("LANGUAGE") or "").strip()
            if name:
                out.append(name)
                src = (r.get("SRC_FILE") or "").strip()
                if src:
                    self._src[name] = src
        return out

    def _file_for(self, name: str):
        # LANG.DBF's SRC_FILE when present, else 'German(Germany)' -> GERMAN.DBF
        # (the first word, before any '(': what the compiler falls back to).
        src = getattr(self, "_src", {}).get(name)
        if src:
            f = _find_file_ci(self.project_dir, os.path.basename(src))
            if f:
                return f
        first = name.split("(")[0].strip()
        return _find_file_ci(self.project_dir, first + ".DBF") if first else None

    # ---- query ----
    def languages(self):
        """Active language display names that have a translation table present."""
        return [n for n in self._names if self._file_for(n)]

    def _table(self, name: str):
        if name not in self._tables:
            tbl = {}
            f = self._file_for(name)
            if f:
                try:
                    _fields, rows = _read_dbf(f, {"LOCAL": language_codepage(name)})
                    for r in rows:
                        r = {k.upper(): v for k, v in r.items()}
                        nat = (r.get("NATIVE") or "").strip()
                        loc = (r.get("LOCAL") or "").strip()
                        if nat and loc:
                            tbl[nat] = loc
                except Exception:  # noqa: BLE001
                    tbl = {}
            self._tables[name] = tbl
        return self._tables[name]

    def translate(self, native: str, name: str) -> str:
        """``native`` rendered in language ``name``; NATIVE fallback when the
        string has no translation in that language (matching the runtime)."""
        return self._table(name).get((native or "").strip()) or native


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register(
    "lang.databases", LanguageDB, clear=LanguageDB.clear_cache, on=("libraries", "refresh", "tests")
)
_caches.register("lang.listings", _LISTING_CACHE, on=("refresh", "tests"))
