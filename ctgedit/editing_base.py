"""Errors and helpers shared by editing.py and the doc mixins."""

from __future__ import annotations

from .infra import workers as _workers


from .format.projects import is_project_dir

import base64  # noqa: F401 (re-exported)
import contextlib
import hashlib
import json
import math  # noqa: F401 (re-exported)
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple  # noqa: F401 (re-exported)

from .format.page import parse_ctg, ParsedCtg, PageObject, parse_object  # noqa: F401 (re-exported)
from .format import writer, factory  # noqa: F401 (re-exported)
from .format import geometry as geom
from .format import inputs  # noqa: F401 (re-exported)
from .model.document import ObjectKind, Page  # noqa: F401 (re-exported)
from .format.textcodec import CODEC  # noqa: F401 (re-exported)
from .format import codes as codes  # noqa: F401 (the doc/ mixins take it from here)
from .format import textcodec
from .format.symbols import SymbolResolver
from .format.fileio import write_atomic
from .features.symbol_sets import SymbolSetEditing, SymbolSetError  # noqa: F401 (re-exported)
from .infra.logsetup import note_exception as _note_exc


#: user text holds characters the page's ANSI code page cannot store (the
#: one class, shared with the project database writers: format/textcodec)
UnencodableTextError = textcodec.UnencodableTextError


def check_page_name(stem: str, folder: str) -> None:
    """Refuse a page name the project's pages.DBF cannot store, before any file is written:
    one longer than its NAME (ValueError) or with characters its ANSI text cannot hold
    (:class:`UnencodableTextError`). A page outside a project (no pages.DBF) is never
    registered and may keep any name."""
    from .format.projects import PAGE_NAME_LEN

    if not is_project_dir(folder):
        return
    if len(stem.encode(textcodec.CODEC, "replace")) > PAGE_NAME_LEN:
        raise ValueError(
            "Page names can be at most %d characters (%s has %d)." % (PAGE_NAME_LEN, stem, len(stem))
        )
    if textcodec.unencodable(stem):
        textcodec.encode_field(stem, "a page name (pages.DBF keeps it as the page's NAME)")


def guard_text(*values) -> None:
    """Raise :class:`UnencodableTextError` naming every character in
    ``values`` that the page code page cannot store (none when such text is stored as
    '?', textcodec.replacing)."""
    if textcodec.replacing():
        return
    bad: list = []
    for v in values:
        if v is None:
            continue
        for ch in textcodec.unencodable(str(v)):
            if ch not in bad:
                bad.append(ch)
    if bad:
        raise UnencodableTextError(
            "%s cannot be stored in a Plant SCADA page (Windows-%d code page). Replace those "
            "characters" % (", ".join("'%s' (U+%04X)" % (c, ord(c)) for c in bad), textcodec.CODEPAGE)
        )


def crlf_text(text):
    """A caption's line breaks as CtDraw stores them: CRLF, never a bare LF. The editors
    hand '\\n' (the model shows captions with '\\n', reader._norm_nl)."""
    if not text or ("\n" not in text and "\r" not in text):
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


class TransformError(ValueError):
    """A rotate / mirror met a record it cannot transform, or an edit would put an object
    where the page cannot store it; the edit was abandoned and the page left exactly as it
    was."""


#: why an edit past the page's coordinates is refused (TransformError)
COORD_REFUSED = (
    "That would put objects outside what a page can store (positions from -32768 to "
    "32767). Nothing was changed."
)


def check_coords(*values) -> None:
    """Raise TransformError when a coordinate of ``values`` (numbers, or sequences of
    them) is outside what a page stores."""
    for v in values:
        if v is None:
            continue
        seq = v if isinstance(v, (list, tuple)) else (v,)
        for x in seq:
            if isinstance(x, (list, tuple)):
                check_coords(x)
            elif not -32768 <= int(x) <= 32767:
                raise TransformError(COORD_REFUSED)


class LibraryLinkError(ValueError):
    """Cut Link / re-pointing a placed symbol is not possible for this
    object; the message says why (the page is unchanged)."""


def fresh_identity(parsed: ParsedCtg, created: bool = False) -> bytes:
    """Give a page a new identity (save stamp and GUID), as CtDraw's save does."""
    from .format.element import ctdraw_timestamp
    from .infra import identity

    stamp = ctdraw_timestamp()
    ident = struct.pack("<I", stamp) + identity.guid_bytes()
    if parsed.page is not None:
        parsed.page.page_identity = ident
    at = parsed.attributes
    if at is not None:
        at.page_identity = ident
        at.ts_modified = stamp
    parsed.container.ts_modified = stamp
    if created:
        parsed.container.ts_created = stamp
    return ident


def prune_link_table(pb) -> list:
    """Drop link table entries nothing on the page uses any more."""
    from .format import genie_place as GP
    from .format import links

    if pb is None:
        return []
    t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
    if t is None:
        if pb.link_count or not any(
            (po.type_code == GP.TEXT_TC and b"@(" in po.body_raw)
            or (po.type_code == 0xFFFF and b"_Library" in po.body_raw)
            for po in pb.objects
        ):
            return []
        # (a page with no table yet whose texts carry labels, or with a placeholder:
        # CtDraw's save writes one)
        t = (getattr(pb, "area_code", None) or "    ", [])
    keep = GP.used_link_entries(pb.objects, t[1], values_only=True)
    keep = GP.with_record_labels(pb.objects, keep)
    keep = GP.with_placeholder_entries(pb.objects, keep)
    keep = GP.record_order_entries(pb.objects, keep)
    if keep == t[1]:
        return []
    pb.link_table_raw, pb.link_count = links.serialize_link_table(t[0], keep)
    return [e for e in t[1] if e not in keep]


def identity_state(parsed: ParsedCtg) -> tuple:
    """What a Save As changes on the document (fresh_identity +
    prune_link_table), so a failed write can put it back."""
    pb, at, ct = parsed.page, parsed.attributes, parsed.container
    return (
        (pb.page_identity, pb.link_table_raw, pb.link_count) if pb is not None else None,
        (at.page_identity, at.ts_modified) if at is not None else None,
        (ct.ts_created, ct.ts_modified),
    )


def set_identity_state(parsed: ParsedCtg, state: tuple) -> None:
    page, attrs, cont = state
    if page is not None and parsed.page is not None:
        parsed.page.page_identity, parsed.page.link_table_raw, parsed.page.link_count = page
    if attrs is not None and parsed.attributes is not None:
        parsed.attributes.page_identity, parsed.attributes.ts_modified = attrs
    parsed.container.ts_created, parsed.container.ts_modified = cont


CLIPBOARD_MIME = "application/x-ctgedit-objects"

#: bumped whenever ctgedit writes a library element: every document's cached
#: SymbolResolver (and placement link info) is rebuilt on its next use
LIBRARY_GENERATION = [0]


def libraries_changed() -> None:
    LIBRARY_GENERATION[0] += 1
    # the shared folder listings and what a derive computed from the libraries / project
    # tables are computed again (View > Refresh must bring back a page as a fresh editor
    # draws it: tests/test_derive_threads.py; the tag formats are keyed by project only, so
    # a table changed by Studio is read again)
    forget_derived()


def forget_derived() -> None:
    """Empty the process caches of what page derives computed from library elements and
    project tables (each is computed again on its next use): infra/caches 'libraries'."""
    from .infra import caches

    caches.clear("libraries")


#: what a library save says while the Graphics Builder runs (ctdraw_cache_warning)
CTDRAW_CACHE_WARNING = (
    "The Graphics Builder is running and still has the old version of this element in memory. "
    "Close it (Tools > Close Graphics Builder) before you edit or place the element there, "
    "or it saves the old version back."
)


def ctdraw_cache_warning() -> str:
    """CtDraw keeps every library element it has loaded in RAM for its whole
    session and never re-reads the file: with a Graphics Builder running, it keeps
    showing (and on its next save of that element WRITES BACK) the old version."""
    try:
        from .backend.graphics_builder import ctdraw_pids

        running = bool(ctdraw_pids())
    except Exception:  # noqa: BLE001 -- swallow-ok: no pywin32: not running
        running = False
    if not running:
        return ""
    return CTDRAW_CACHE_WARNING


# ---------------------------------------------------------------------------
# The fast-display (.ctF) trust check.
#
# A save rebuilds a page's .ctF natively only when the native writer
# reproduces CtDraw's .ctF of the page's PREVIOUS version (build_ctf of the
# .ctg on disk == the .ctF on disk), else it removes the .ctF so the runtime
# draws the .ctg (EditableDocument._update_runtime_files). The check is a
# whole page build, so the editor runs it ahead of the first save, when it is
# idle after showing the page, in a separate PROCESS (``ctgedit --ctf-check``).
# A verdict is only used for
# exactly the bytes it checked (sha1 of both files); anything else (no verdict
# yet, a failed process, other bytes) falls back to computing it in the save.


def ctf_matches(ours: bytes, disk: bytes) -> bool:
    """Does our .ctF match CtDraw's, byte for byte or up to timestamps?"""
    if ours == disk:
        return True
    if len(ours) != len(disk) or len(ours) < 0x10:
        return False
    try:
        from .format.container import Container

        c = Container.parse(ours)
    except Exception:  # noqa: BLE001 -- swallow-ok: not a container: never equal
        return False
    if c.word2 < 0x0C:  # a legacy page head: no masking
        return False
    fixed = bytearray(ours)
    fixed[4:12] = disk[4:12]
    for name in ("page", "attributes"):
        blk = c.block(name)
        if blk is None or blk.size < 0x24:
            return False
        o = blk.offset + 0x20
        if ours[o : o + 4] != ours[8:12] or disk[o : o + 4] != disk[8:12]:
            return False
        fixed[o : o + 4] = disk[o : o + 4]
    if bytes(fixed) == disk:
        return True
    # the state of the SESSION that wrote it, not of the page: the background record's
    # colour pairs (the colour selector's colour) and the save times in an ActiveX
    # control's storage (the control saves itself at every write)
    from .format import ctf as CTF

    for a, b in CTF.session_ranges(disk, c):
        fixed[a:b] = disk[a:b]
    return bytes(fixed) == disk


def ctf_verdict(ctg: bytes, old_ctf: bytes, page_path: str, resolver=None) -> bool:
    """build_ctf(``ctg``) reproduces ``old_ctf`` (:func:`ctf_matches`)."""
    from .format import ctf as CTF

    return ctf_matches(CTF.build_ctf(ctg, page_path, resolver=resolver), old_ctf)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def ctf_check_command(page_path: str, out_path: str) -> List[str]:
    """The command line of the check process: ``ctgedit.exe --ctf-check``
    in the frozen build, else this Python running ctf_check_main (no Qt)."""
    from .infra import workers

    return workers.command("--ctf-check", "ctgedit.editing", "ctf_check_main", [page_path, out_path])


def ctf_check_main(argv=None) -> int:
    """``ctgedit --ctf-check <page.ctg> <result.json>``: the trust check in
    its own process. Reads both files, writes {"ctg": sha1, "ctf": sha1,
    "ok": verdict} -- or {"error": ...} when the build raised (the check then
    counts as failed, as an exception in the in-process check does)."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--ctf-check"]:
        args = args[1:]
    if len(args) != 2:
        return 2
    page, out = args
    res: dict = {}
    try:
        with open(page, "rb") as fh:
            ctg = fh.read()
        with open(page[:-1] + "F", "rb") as fh:
            old = fh.read()
        res["ctg"], res["ctf"] = sha1(ctg), sha1(old)
        res["ok"] = bool(ctf_verdict(ctg, old, page, resolver=SymbolResolver.for_page_env(page)))
    except Exception as e:  # noqa: BLE001 -- swallow-ok: reported to the editor
        res["error"] = "%s: %s" % (type(e).__name__, e)
    try:
        write_atomic(out, json.dumps(res).encode("utf-8"))
    except OSError:
        return 1
    return 0 if "error" not in res else 1


class CtfPrecheck(_workers.CheckProcess):
    """One page's fast-display trust check, run in a helper process."""

    KIND = "ctf"
    FOLDER = "ctfcheck"
    SWEEP_SUFFIX = ".json"
    #: the check process now running (the editor starts the next when it ends)
    _running: List["CtfPrecheck"] = []
    _swept = False
    #: seconds a save waits for a running check before computing it itself
    WAIT = 30.0

    def __init__(self, box: dict, path: str, resolver=None):
        self.box = box
        self.path = path
        self._resolver = resolver  # () -> the document's resolver
        self.proc = None
        self.out = None
        self.done = False

    def started(self) -> bool:
        return self.proc is not None or self.done

    def start(self) -> bool:
        """Start the check process now. False = another one is running (try
        again later); True when it runs, ran, or is not needed any more."""
        if self.started():
            return True
        if not self.may_start():
            return False
        try:
            self.out = self.answer_file()
            self.launch(ctf_check_command(self.path, self.out))
        except Exception:  # noqa: BLE001 -- the save computes it itself
            _note_exc("editing_base.CtfPrecheck.start", path=self.path)
            self.proc = None
            self.done = True
        return True

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def cancel(self) -> None:
        """Not needed any more (the page was saved / closed): stop the process."""
        self.kill()
        self._discard_out()
        self.done = True

    def _discard_out(self) -> None:
        if self.out:
            with contextlib.suppress(OSError):
                os.remove(self.out)

    # -- the verdict -------------------------------------------------------------
    def join(self, timeout: Optional[float] = None) -> Optional[bool]:
        """The verdict (also stored in ``box['ok']``): the process's when it
        checked exactly the box's bytes, else computed here from the files --
        when they still are those bytes. None = no verdict."""
        if self.box.get("ok") is not None or self.done and self.proc is None:
            return self.box.get("ok")
        ok = None
        if self.proc is not None:
            try:
                self.proc.wait(self.WAIT if timeout is None else timeout)
            except Exception:  # noqa: BLE001 -- swallow-ok: too slow: compute it here
                with contextlib.suppress(Exception):
                    self.proc.kill()
            res = self.read_answer()
            self._discard_out()
            if (
                isinstance(res, dict)
                and res.get("ctg") == self.box.get("ctg")
                and res.get("ctf") == self.box.get("ctf")
            ):
                ok = False if "error" in res else bool(res.get("ok"))
                if "error" in res:
                    from .infra import logsetup

                    logsetup.get("ctf").warning(
                        "fast-display check of %s failed: %s", self.path, res["error"]
                    )
        if ok is None:
            ok = self._compute()
        self.done = True
        self.box["ok"] = ok
        return ok

    def _compute(self) -> Optional[bool]:
        """The verdict computed in this process (the files must still be the
        bytes the box names)."""
        try:
            with open(self.path, "rb") as fh:
                ctg = fh.read()
            with open(self.path[:-1] + "F", "rb") as fh:
                old = fh.read()
        except OSError:
            return None
        if sha1(ctg) != self.box.get("ctg") or sha1(old) != self.box.get("ctf"):
            return None
        try:
            res = self._resolver() if self._resolver is not None else None
            return ctf_verdict(ctg, old, self.path, resolver=res)
        except Exception:  # noqa: BLE001
            from .infra import logsetup

            logsetup.get("ctf").warning("fast-display check of %s failed", self.path, exc_info=True)
            return False


def create_super_genie_pages(
    project_dir: str, targets, resolver, *, thumbnail=None, placing: bool = True
) -> List[dict]:
    """Create the super genie pages in ``targets`` the project doesn't have yet."""
    from .format import supergenie as SG

    out: List[dict] = []
    if not targets:
        return out
    pdir = os.path.abspath(project_dir)
    is_project = is_project_dir(pdir)
    background = None
    try:
        from .io import projectcit

        bg = (projectcit.page_defaults(pdir) or {}).get("background")
        background = (bg, bg) if bg is not None else None
    except Exception:  # noqa: BLE001 -- no Page Defaults: the page gets no colour
        from .infra.logsetup import note_exception

        note_exception("editing_base.create_super_genie_pages")
    for lib, sgel in targets:
        name = SG.page_name(sgel)
        rep = {
            "name": name,
            "element": "%s.%s" % (lib, sgel),
            "status": "skipped",
            "path": os.path.join(pdir, name + ".ctg"),
            "detail": "",
        }
        out.append(rep)
        if not name:
            rep["detail"] = "the super genie has no page name"
            continue
        if not is_project:
            rep["detail"] = "%s is not a project folder (no pages.DBF)" % pdir
            continue
        if placing and os.path.basename(pdir).lower() == SG.NO_INSTANCE_PROJECT.lower():
            rep["detail"] = "placing a genie never creates super genie pages in the Include project"
            continue
        try:
            where = SG.page_in_chain(pdir, name)
        except Exception:  # noqa: BLE001
            _note_exc("editing_base.create_super_genie_pages", page=name)
            where = None
        if where is not None:
            rep.update(
                status="exists",
                path=os.path.join(where, name + ".ctg"),
                detail="the page exists in %s" % os.path.basename(where),
            )
            continue
        if os.path.exists(rep["path"]):
            # a .ctg with no pages.DBF row: CtDraw would overwrite it; a file
            # the user may still want is left alone and reported
            rep.update(
                status="exists",
                detail=(
                    "%s.ctg is in the project folder but not registered in the project, so it "
                    "was left as it is. Delete or rename it to have the page created." % name
                ),
            )
            continue
        try:
            sg = SG.resolve(resolver, lib, sgel)
            if sg is None:
                rep.update(
                    status="missing", detail="%s.%s is not on this project's include path" % (lib, sgel)
                )
                continue
            data = SG.new_page_bytes(sg, SG.link_text(lib, sgel), background=background)
            doc = _ed().EditableDocument.from_bytes(data, rep["path"])
            # CtDraw writes the new page's fast display too; the native
            # writer reproduces it for these pages
            doc._ctf_new_page = True
            if thumbnail is not None:
                with contextlib.suppress(Exception):
                    blob = thumbnail(doc)
                    if blob:
                        doc.parsed.page.thumbnail = blob
            doc.save()
            rep.update(status="created", detail=doc.save_warning or "")
            rep["files"] = list(getattr(doc, "save_files", None) or [])
        except Exception as e:  # noqa: BLE001 -- the placement / save never fails on it
            _note_exc("editing_base.create_super_genie_pages")
            rep.update(status="failed", detail="%s: %s" % (type(e).__name__, e))
    from .infra import logsetup

    for r in out:
        logsetup.get("supergenie").info("%s (%s): %s %s", r["name"], r["element"], r["status"], r["detail"])
    return out


#: the Access > General keys CtDraw stores 'value<User>' on a GENIE MEMBER
#: (the fields its member sheet leaves editable; io/pgregister strips it)
USER_MARKED_KEYS = ("DESC", "COMMENT", "AREA", "PRIV", "DEVICE")

#: why a composite genie's options cannot be changed (CtDraw's page load
#: re-composes a composite from the cache.<hash> element its root links to and
#: restores every option on the root and its members)
COMPOSITE_LOCKED = (
    "A composite genie's options come from its composition cache. Change them with Edit Composite Genie."
)
#: where a composite genie can go (help 1172148_1: a page, template or super
#: genie, never a genie or a symbol)
COMPOSITE_NOT_HERE = (
    "A composite genie can only be placed on a page, a template or a super genie, not in a genie or a symbol."
)


def nesting_refusal(chain, host: str, kind: str = "genie") -> str:
    """Why genie ``chain[0]`` can't go into ``host`` (it would contain itself)."""
    from .format.genie_place import cycle_text

    chain = list(chain)
    if len(chain) <= 1:
        return "A %s cannot be placed in itself: %s is the %s you are editing." % (kind, host, kind)
    return "%s cannot be placed in %s, because it holds a copy of %s: %s." % (
        chain[0],
        host,
        host,
        cycle_text(chain),
    )


def self_link_refusal(chain) -> str:
    """Why an element whose records hold a copy of itself cannot be saved (``chain`` = the
    genies from it back to itself: genie_place.records_nesting_chain), as CtDraw refuses the
    save ('Attempt to save page with a link to itself')."""
    from .format.genie_place import cycle_text

    chain = list(chain)
    return (
        "%s holds a copy of itself (%s), so it cannot be saved. Delete one of the copies "
        "in that chain first." % (chain[-1], cycle_text(chain))
    )


#: library classes whose sheet has Input > Keyboard Commands but no Touch
#: page (page mask bit 0x400, not 0x200: Trend / TextAn 0x81070c02, Cicode
#: 0x80070c00): their key sequences are Keyboard CHILD records
KEYBOARD_ONLY_TCS = {0x1003: "trend", 0x1006: "digital text", 0x1009: "Cicode object"}
#: the longest object name (CtDraw's Access > General field takes 32)
OBJECT_NAME_MAX = 32
#: characters an object name cannot contain (CtDraw; the first
#: character can be none of these, nor a digit or '_')
_NAME_FORBIDDEN = frozenset("<>:\"/\\|?*.!^& ()-+~`[]{};',=%$@#")


def object_name_rule_problem(name: str) -> str:
    """Why ``name`` is not a valid object name ('' = it is, or it is empty =
    no name): CtDraw's check -- the first character is not a
    digit, '_' or one of <>:"/\\|?*.!^& ()-+~`[]{};',=%$@#, and no later
    character is one of those -- and the 32-character field."""
    if not name:
        return ""
    if len(name) > OBJECT_NAME_MAX:
        return "The name is too long: %d characters. The maximum is %d; shorten it." % (
            len(name),
            OBJECT_NAME_MAX,
        )
    first = name[0]
    if (
        first == "_"
        or first in "0123456789"
        or first in _NAME_FORBIDDEN
        or any(c in _NAME_FORBIDDEN for c in name[1:])
    ):
        return (
            "'%s' is not a valid object name. A name must begin with a letter and "
            "contain only letters, numbers and '_' (no spaces or punctuation)." % name
        )
    return ""


def unique_object_name(name: str, taken) -> str:
    """'Name' -> 'Name_1', 'Name_3' -> 'Name_4': CtDraw's rename for clashing pasted names."""
    while name and taken(name):
        cut = name.rfind("_")
        n = 0
        if cut >= 0:
            import re as _re

            m = _re.match(r"\s*([+-]?\d+)", name[cut + 1 :])
            n = int(m.group(1)) if m else 0
        name = name + "_1" if n == 0 else "%s%d" % (name[: cut + 1], n + 1)
        if len(name) > OBJECT_NAME_MAX:
            return ""
    return name


#: the Metadata page's limits: a name of 253 characters (help 1172180), a
#: value of 255 (the list's 256-byte cells)
METADATA_NAME_MAX = 253
METADATA_VALUE_MAX = 255
#: Cicode words a metadata name cannot be (help 1172180: 'excluding ...
#: reserved keywords')
_CICODE_RESERVED = frozenset(
    "AND OR NOT MOD BITAND BITOR BITXOR IF THEN ELSE END WHILE DO FOR TO SELECT CASE IS "
    "RETURN FUNCTION GLOBAL MODULE PUBLIC PRIVATE INT REAL STRING OBJECT QUALITY "
    "TIMESTAMP".split()
)


def metadata_problem(items) -> Optional[Tuple[int, str]]:
    """(row, message) of the first metadata row CtDraw would refuse, or None."""
    import re as _re

    seen = set()
    for row, (name, value) in enumerate(items):
        n = row + 1
        if not name:
            return row, ("Metadata row %d has a value but no name. Enter a name or delete the row." % n)
        if len(name) > METADATA_NAME_MAX:
            return row, (
                "The metadata name in row %d is too long: %d characters. The "
                "maximum is %d." % (n, len(name), METADATA_NAME_MAX)
            )
        core = _re.sub(r"%[^%]*%", "", name)
        if (
            name.count("%") % 2
            or not _re.fullmatch(r"[A-Za-z0-9_]*", core)
            or name.upper() in _CICODE_RESERVED
        ):
            return row, (
                "'%s' is not a valid metadata name (row %d). Use only letters, "
                "numbers and '_' (no spaces or punctuation), and not a Cicode "
                "keyword. Genie substitutions such as %%Tag%% are allowed." % (name, n)
            )
        if name.lower() in seen:
            return row, (
                "The metadata name '%s' is used twice (row %d). Every name must be "
                "different on the same object." % (name, n)
            )
        seen.add(name.lower())
        if len(value) > METADATA_VALUE_MAX:
            return row, (
                "The metadata value in row %d is too long: %d characters. The "
                "maximum is %d." % (n, len(value), METADATA_VALUE_MAX)
            )
    return None


def name_key(po: PageObject) -> Tuple[str, str]:
    """(key, value) of the bag pair holding an object's name."""
    pairs = writer.value_bag(po)
    if pairs is None:
        pairs = [kv for bag in po.bags for kv in bag.pairs]
    legacy = None
    for k, v in pairs:
        if k.upper() == "__AN_NAME__":
            return k, v
        if k == "NAME" and legacy is None:
            legacy = (k, v)
    return legacy or ("", "")


def rename_header(po: PageObject, name: str) -> bool:
    """The geometry-header name splice (geometry.set_object_name) with the
    record's bag view re-scanned: every byte after the name moves."""
    ok = geom.set_object_name(po, name)
    if ok:
        writer.refresh_object_bags(po)
    return ok


#: CtDraw's limits for the pages.DBF columns Page Properties edits (the
#: General / Events pages' field lengths); a
#: project table's own column width wins (CLUSTER: CtDraw's field takes 31, the
#: column holds 16)
PAGE_FIELD_LIMITS = {
    "TITLE": 64,
    "COMMENT": 250,
    "PREV_PAGE": 64,
    "NEXT_PAGE": 64,
    "PARENT": 64,
    "AREA": 16,
    "LOGDEVICE": 16,
    "SCANTIME": 10,
    "CLUSTER": 16,
    "CINHERIT": 8,
    "IGNOREVQ": 8,
    "CON_TYPE": 20,
    "ENTRY_FUNC": 254,
    "EXIT_FUNC": 254,
    "BGND_FUNC": 254,
    "READY_FUNC": 254,
    "ACT_FUNC": 254,
    "DEACT_FUNC": 254,
}
PAGE_FIELD_LABELS = {
    "TITLE": "window title",
    "COMMENT": "description",
    "PREV_PAGE": "previous page",
    "NEXT_PAGE": "next page",
    "PARENT": "parent page",
    "AREA": "area",
    "LOGDEVICE": "log device",
    "SCANTIME": "page scan time",
    "CLUSTER": "cluster",
    "CINHERIT": "cluster inheritance",
    "IGNOREVQ": "Ignore Quality setting",
    "CON_TYPE": "content type",
    "ENTRY_FUNC": "'On page entry' command",
    "EXIT_FUNC": "'On page exit' command",
    "BGND_FUNC": "'While page shown' command",
    "READY_FUNC": "'On page shown' command",
    "ACT_FUNC": "'On activate' command",
    "DEACT_FUNC": "'On deactivate' command",
}
#: a page keyboard command's pgkey columns and their limits (the Keyboard
#: page: key sequence 32, command 254, area / privilege 16, log message 32;
#: the pgkey column widths)
PAGE_KEY_LIMITS = {"KEY_SEQ": 32, "CMD": 254, "AREA": 16, "PRIV": 16, "MSG_LOG": 32}
PAGE_KEY_LABELS = {
    "KEY_SEQ": "key sequence",
    "CMD": "command",
    "AREA": "command area",
    "PRIV": "privilege level",
    "MSG_LOG": "log message",
}


def find_options(options: dict) -> dict:
    """FindOptions keyword arguments: ``scopes`` as a frozenset, a filter given as a pattern
    or an (include, exclude) pair as a NameFilter."""
    out = dict(options)
    if "scopes" in out:
        out["scopes"] = frozenset(out["scopes"])
    for k in ("pages", "fields", "genies", "objects"):
        v = out.get(k)
        if isinstance(v, str):
            out[k] = _name_filter(v)
        elif isinstance(v, tuple):
            out[k] = _name_filter(*v)
    return out


def _name_filter(include: str = "", exclude: str = ""):
    from .features.find_replace import NameFilter

    return NameFilter(include, exclude)


# (defined with the register they come from; the save raises them)
from .io.pgregister import PageRecordError, ProjectDatabaseInUse  # noqa: E402,F401
from .format.projectlock import PageBusyError, ProjectBusyError  # noqa: E402,F401


class PageChangedOnDisk(OSError):
    """A batch save (Update Pages, Replace All in closed pages) found the page file changed
    since the document was read from it (another save wrote it meanwhile): nothing was
    written. Open the page again and repeat the change."""

    def __init__(self, path: str):
        super().__init__(
            "%s was changed by another save while it was being updated."
            % os.path.splitext(os.path.basename(path))[0]
        )
        self.path = path


def file_identity(path: str):
    """(file id, mtime ns, size) of ``path``, None when it cannot be read. os.replace gives
    a rewritten file a new id, so a rewrite of the same size in the same clock tick still
    differs."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


#: the identity of bytes read while the file kept changing: it matches no file
UNSTABLE_IDENTITY = ("unstable",)


def read_with_identity(path: str):
    """(the bytes of ``path``, the :func:`file_identity` they came from): the file is
    stat'ed before and after the read and read again (at most 3 times) when the two
    differ; bytes read while it kept changing get :data:`UNSTABLE_IDENTITY`."""
    data = b""
    for _k in range(3):
        before = file_identity(path)
        with open(path, "rb") as fh:
            data = fh.read()
        after = file_identity(path)
        if before == after:
            return data, after
    return data, UNSTABLE_IDENTITY


@dataclass
class Command:
    label: str
    do: Callable[[], None]
    undo: Callable[[], None]
    #: what the step keeps alive, roughly (bytes; -1 = not measured yet: command_bytes)
    nbytes: int = -1
    #: when the step was made (time.time(): the History panel's time column)
    when: float = field(default_factory=time.time)
    #: what the step changed, as the editor window saw it: (the records of the units
    #: selected before the step, the records selected after it), by identity (Undo shows
    #: the first, Redo the second: ui/panels/history_dock.HistoryCommands); None = not known
    targets: Any = None
    #: made by the editor itself (an automatic update, ui/window/files._note_auto_change):
    #: undone, it is no step of the user's to redo that an automatic change must wait for
    automatic: bool = False
    #: how many steps were made part of it since (EditableDocument.fold_last_step): the
    #: Simulate window tells such a change of the step on top from one of the user's
    folded: int = 0


#: steps always kept, whatever they hold (the undo memory: editing.UNDO_BYTES_TOTAL and
#: editing._undo_page_cap)
UNDO_KEEP_MIN = 10


#: values a size walk counts in their container and does not visit
_SCALARS = (int, float, bool, type(None))


def command_bytes(cmd: Command) -> int:
    """About how many bytes a step's do / undo closures keep (saved record states, payloads,
    copied lists), measured once."""
    if cmd.nbytes >= 0:
        return cmd.nbytes
    seen: set = set()
    total = 0
    stack = [(cmd.do, 0), (cmd.undo, 0)]
    while stack:
        v, depth = stack.pop()
        if id(v) in seen or depth > 6:
            continue
        seen.add(id(v))
        if isinstance(v, (bytes, bytearray, memoryview)):
            total += len(v)
        elif isinstance(v, str):
            total += len(v)
        elif hasattr(v, "runs") and hasattr(v, "undo"):  # doc.geometry._BlockDiff
            total += len(v)
        elif isinstance(v, dict):
            total += 64 * len(v)
            stack.extend((x, depth + 1) for x in v.values() if not isinstance(x, _SCALARS))
        elif isinstance(v, (list, tuple)):
            total += 8 * len(v)
            stack.extend((x, depth + 1) for x in v if not isinstance(x, _SCALARS))
        elif callable(v):
            for c in getattr(v, "__closure__", None) or ():
                try:
                    stack.append((c.cell_contents, depth + 1))
                except ValueError:  # an empty cell
                    pass
    cmd.nbytes = total
    return total


class _ForestNode:
    """One record's place in a :class:`RecordForest`."""

    __slots__ = ("parent", "members", "is_genie", "kind", "raw")

    def __init__(self, parent):
        self.parent = parent
        self.members: List[int] = []
        self.is_genie = False
        self.kind = None  # (no drawing model: callers test the records)
        self.raw: dict = {}


class RecordForest:
    """The record forest of a page without its drawing model: ``objects[i].parent`` /
    ``.members`` and the Page navigation EditableDocument uses (roots, root_of,
    ancestors, subtree). EditableDocument.from_bytes(records_only=True) holds one as
    ``page`` (a helper process that edits records and never draws them)."""

    def __init__(self, records, name: str = ""):
        from .format import placement as P

        parents = P.forest_parents(records)
        self.objects = [_ForestNode(par) for par in parents]
        for i, par in enumerate(parents):
            if par is not None:
                self.objects[par].members.append(i)
        self.name = name

    def roots(self) -> List[int]:
        return [i for i, o in enumerate(self.objects) if o.parent is None]

    def root_of(self, index: int) -> int:
        cur = index
        while 0 <= cur < len(self.objects) and self.objects[cur].parent is not None:
            cur = self.objects[cur].parent
        return cur

    def ancestors(self, index: int) -> List[int]:
        out = []
        cur = self.objects[index].parent if 0 <= index < len(self.objects) else None
        while cur is not None:
            out.append(cur)
            cur = self.objects[cur].parent
        return out

    def subtree(self, index: int) -> List[int]:
        out = [index]
        stack = list(self.objects[index].members) if index < len(self.objects) else []
        while stack:
            i = stack.pop()
            out.append(i)
            stack.extend(self.objects[i].members)
        out.sort()
        return out


def ini_back_color() -> str:
    """citect.ini [CtDraw.RSC] BackColor ('' when it cannot be read)."""
    try:
        from .io.citect_config import ini_value

        return ini_value("CtDraw.RSC", "BackColor", "")
    except Exception:  # noqa: BLE001 -- swallow-ok: no install or an unreadable ini: no colour
        return ""


@dataclass
class DocumentUiState:
    """What the editor window keeps about a document (``doc.ui``), not part of the page:
    the changes made at open (the bar that lists them), the recovery copy, the open and
    display-check diagnostics, the questions already asked."""

    #: [(what, undo step)] changed automatically at open (the bar lists them)
    auto_changes: list = field(default_factory=list)
    #: how many automatic changes were listed since it opened: one that became part of the
    #: step of the same name before it counts too (ui/window/files._note_auto_change)
    auto_count: int = 0
    auto_bar_dismissed: bool = False
    #: the recovery copy: its key, the change signature and times (ui/recovery.py)
    autosave_key: Optional[str] = None
    as_sig: Any = None
    as_changed_at: Optional[float] = None
    as_saved_at: Optional[float] = None
    as_saved_sig: Any = None
    #: a super genie page opened read-only (Revert / reload keep it so)
    supergenie_view: bool = False
    #: the question about saving into a supplied library was answered
    shipped_ok: bool = False
    #: the SA workspace check report shown for it
    sa_report: Any = None
    #: the Tools > Check Page report shown for it, and the token of its engine phase
    page_report: Any = None
    page_check_token: Any = None
    #: diagnostics of the open (diag_ui / diag/open_report) and the display check
    open_diag: Optional[dict] = None
    diag_report: Any = None
    diag_open_logged: bool = False
    display_check: Any = None
    #: the file stamp the 'changed on disk' question was asked for
    disk_asked: Any = None
    #: a damaged page may be saved in place (asked once)
    damage_ok: bool = False
    #: genie sources already looked at for this document (stale genies)
    genie_sources_done: dict = field(default_factory=dict)
    #: the versions ((library, element), its link-table entry; lower case) an automatic
    #: update took the library symbols placed in it to: an update the user undid is not made
    #: again until the symbol is saved again (ui/window/files._placed_symbol_step). Kept
    #: apart from genie_sources_done, which a failed or cancelled genie update empties
    symbols_done: frozenset = frozenset()
    #: the template steps a template save made in the page's tab ({'lib.template', lower
    #: case: a digest of the records it gave the template placements}): the chain's end
    #: leaves a page whose step was undone (ui/window/library._chain_templates_in_tabs)
    template_steps: dict = field(default_factory=dict)
    #: a remembered genie update applied before the open's derive: (names, undo step), said
    #: by the open checks (files._genie_answer_said)
    genie_preapplied: Any = None
    #: the genies a background update is composing again: (object count, root indices);
    #: the canvas marks them until the update lands
    genies_updating: Any = None
    #: the open's template step runs in the genie worker: its symbol, placeholder genie and
    #: genie steps wait for it to land, as CtDraw's load meets the template first
    #: (ui/window/tabs._open_steps_after_template)
    steps_after_template: bool = False
    #: the Simulate window's page model of it, with the edit serials it was made at
    sim_model_cache: Any = None
    #: View > Preview States: the values set by hand, by condition (features/state_preview)
    preview_values: dict = field(default_factory=dict)
    #: the canvas's Previous View / Next View for it: [(QTransform, scene centre)], at most
    #: 50 each (ui/editor/viewnav.py)
    view_history: list = field(default_factory=list)
    view_future: list = field(default_factory=list)
    #: an earlier version of a page shown read-only in a tab (File > Page History > Open
    #: Read-only): the infra.history.Version it shows
    version_view: Any = None
    #: View > Mark Changes compares with this earlier version (an infra.history.Version)
    #: instead of the saved file (File > Page History > Compare); None = the saved file
    changes_base: Any = None
    #: when the document was opened (the History panel's first row)
    opened_at: float = field(default_factory=time.time)


def ui_state(doc) -> DocumentUiState:
    """``doc.ui`` -- or a fresh state for something that is no document (a tab that is not
    open yet): reading it gives the defaults."""
    st = getattr(doc, "ui", None)
    return st if isinstance(st, DocumentUiState) else DocumentUiState()


def _ed():
    """ctgedit.editing (EditableDocument / ElementDocument are defined there, after this
    module is imported)."""
    from . import editing

    return editing
