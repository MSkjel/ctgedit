"""Page name checks for Save As and New."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from ...format.textcodec import CODEC

NAME_LIMIT = 64
_ILLEGAL = set('\\/:*?"<>|')
_RESERVED = (
    {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}
)

OK, WARN, ERROR = "ok", "warn", "error"


@dataclass(frozen=True)
class Verdict:
    level: str  # ok | warn | error
    message: str
    collision: str = ""  # path of the existing page the name would replace

    @property
    def ok(self) -> bool:
        return self.level != ERROR


def normalise_page_name(text: str) -> str:
    """Drop a typed ``.ctg`` suffix (spaces are kept: the validator reports a
    leading / trailing space instead of silently changing the name)."""
    n = text or ""
    if n.lower().endswith(".ctg"):
        n = n[:-4]
    return n


def validate_page_name(
    name: str,
    project_dir: Optional[str] = None,
    existing: Optional[Dict[str, str]] = None,
    registered: Optional[Iterable[str]] = None,
    has_pages_dbf: Optional[bool] = None,
    mode: str = "save",
    elsewhere: Optional[Dict[str, str]] = None,
) -> Verdict:
    """Check a page name for ``project_dir``."""
    n = normalise_page_name(name)
    if not n.strip():
        return Verdict(ERROR, "Enter a page name.")
    if n[0].isspace():
        return Verdict(ERROR, "A page name cannot start with a space.")
    bad = sorted({c for c in n if c in _ILLEGAL or ord(c) < 32 or ord(c) == 127})
    if bad:
        shown = " ".join(c if c.isprintable() else "\\x%02x" % ord(c) for c in bad)
        return Verdict(ERROR, "Not allowed in a page name: %s" % shown)
    stem_low = n.lower().split(".")[0]
    if stem_low in _RESERVED:
        return Verdict(ERROR, "'%s' is a name Windows reserves for a device." % n)
    if n.endswith((".", " ")):
        return Verdict(ERROR, "A page name cannot end with a dot or a space.")
    try:
        enc = n.encode(CODEC, "strict")
    except UnicodeEncodeError as e:
        bad_chars = "".join(sorted(set(n[e.start : e.end])))
        return Verdict(ERROR, "These characters cannot be stored: %s" % bad_chars)
    if has_pages_dbf is None:
        has_pages_dbf = bool(project_dir) and any(
            os.path.isfile(os.path.join(project_dir, fn)) for fn in ("pages.DBF", "pages.dbf")
        )
    if has_pages_dbf and len(enc) > NAME_LIMIT:
        return Verdict(ERROR, "Too long: %d of %d characters" % (len(enc), NAME_LIMIT))
    if existing is None:
        existing = {}
        if project_dir:
            try:
                for fn in os.listdir(project_dir):
                    if fn.lower().endswith(".ctg"):
                        existing[fn[:-4].lower()] = os.path.join(project_dir, fn)
            except OSError:
                pass
    low = n.lower()
    hit = existing.get(low)
    if hit:
        if mode == "new":
            return Verdict(ERROR, "'%s' already exists in this project." % n, hit)
        return Verdict(WARN, "'%s' already exists. Saving replaces it." % n, hit)
    other = (elsewhere or {}).get(low[:NAME_LIMIT])
    if other:
        return Verdict(WARN, "Project %s (compiled with this one) also has a page '%s'." % (other, n))
    reg = {r.lower() for r in (registered or ())}
    if has_pages_dbf and low[:NAME_LIMIT] in reg:
        return Verdict(WARN, "'%s' is in pages.DBF but its file is missing." % n)
    if "." in n:
        return Verdict(WARN, "A dot in a page name looks like a library reference.")
    if n.startswith("!"):
        return Verdict(WARN, "Names starting with '!' are system pages.")
    tail = "%d / %d characters" % (len(enc), NAME_LIMIT) if has_pages_dbf else "%d characters" % len(enc)
    return Verdict(OK, tail)


def unique_page_name(base: str, existing: Iterable[str]) -> str:
    """``base`` with the first free ``_2``, ``_3``... suffix (case-insensitive)."""
    taken = {e.lower() for e in existing}
    base = normalise_page_name(base).strip() or "NewPage"
    if base.lower() not in taken:
        return base
    stem, i = base, 2
    m = re.match(r"^(.*\D)_(\d{1,3})$", base)  # 'Mixer_2' -> 'Mixer_3', not 'Mixer_2_2'
    if m:
        stem, i = m.group(1), int(m.group(2)) + 1
    # keep within the 64-character registration limit
    while True:
        suffix = "_%d" % i
        cand = stem[: NAME_LIMIT - len(suffix)] + suffix
        if cand.lower() not in taken:
            return cand
        i += 1
