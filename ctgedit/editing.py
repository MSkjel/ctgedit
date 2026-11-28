"""EditableDocument: the editable page or element, tying the file format to the render model."""

from __future__ import annotations

import tempfile

from .infra.paths import lib_path_dirs, path_key


from .format.projects import is_project_dir

import bisect
import hashlib
import weakref

from .editing_base import (
    Callable,
    CODEC,
    contextlib,
    create_super_genie_pages,
    crlf_text,
    ctdraw_cache_warning,
    CtfPrecheck,
    dataclass,
    DocumentUiState,
    field,
    file_identity,
    geom,
    guard_text,
    libraries_changed,
    List,
    metadata_problem,
    Optional,
    os,
    Page,
    PageObject,
    parse_ctg,
    ParsedCtg,
    read_with_identity,
    RecordForest,
    self_link_refusal,
    Sequence,
    struct,
    SymbolSetEditing,
    textcodec,
    Tuple,
    UNDO_KEEP_MIN,
    write_atomic,
    writer,
    CLIPBOARD_MIME,  # noqa: F401 (re-exported)
    COMPOSITE_LOCKED,  # noqa: F401 (re-exported)
    COMPOSITE_NOT_HERE,  # noqa: F401 (re-exported)
    ctf_check_main,  # noqa: F401 (re-exported)
    ctf_matches,  # noqa: F401 (re-exported)
    forget_derived,  # noqa: F401 (re-exported)
    LibraryLinkError,  # noqa: F401 (re-exported)
    object_name_rule_problem,  # noqa: F401 (re-exported)
    PAGE_FIELD_LABELS,  # noqa: F401 (re-exported)
    PAGE_FIELD_LIMITS,  # noqa: F401 (re-exported)
    PageBusyError,  # noqa: F401 (re-exported)
    PageChangedOnDisk,  # noqa: F401 (re-exported)
    ProjectBusyError,  # noqa: F401 (re-exported)
    ProjectDatabaseInUse,  # noqa: F401 (re-exported)
    SymbolSetError,  # noqa: F401 (re-exported)
    TransformError,  # noqa: F401 (re-exported)
    ui_state,  # noqa: F401 (re-exported)
    UnencodableTextError,  # noqa: F401 (re-exported)
    unique_object_name,  # noqa: F401 (re-exported)
    USER_MARKED_KEYS,  # noqa: F401 (re-exported)
)
from .editing_base import (  # noqa: F401  (the private names, re-exported)
    Command,
    LIBRARY_GENERATION,
    check_page_name,
    command_bytes,
    ctf_verdict,
    fresh_identity,
    name_key,
    parse_object,
    prune_link_table,
    sha1,
)
from .doc.geometry import GeometryEdits
from .doc.properties import PropertyEdits
from .doc.library import LibraryEdits
from .doc.genieswap import GenieSwapEdits
from .doc.structure import StructureEdits
from .doc.pageops import PageEdits
from .doc.history import HistoryAndSave
from .infra.logsetup import note_exception as _note_exc


def _rising_run(pairs: list) -> list:
    """The longest subsequence of ``pairs`` (in their order) whose second values rise (the
    patience sort, with back links)."""
    tails: list = []
    tails_at: list = []
    back = [None] * len(pairs)
    for k, (_r, o) in enumerate(pairs):
        p = bisect.bisect_left(tails, o)
        if p == len(tails):
            tails.append(o)
            tails_at.append(k)
        else:
            tails[p] = o
            tails_at[p] = k
        back[k] = tails_at[p - 1] if p else None
    out = []
    k = tails_at[-1] if tails_at else None
    while k is not None:
        out.append(pairs[k])
        k = back[k]
    return out[::-1]


@dataclass(eq=False)
# the edits by topic are mixins in ctgedit/doc; the fields, lifecycle and helpers are here
class EditableDocument(
    GeometryEdits,
    PropertyEdits,
    LibraryEdits,
    GenieSwapEdits,
    StructureEdits,
    PageEdits,
    HistoryAndSave,
    SymbolSetEditing,
):
    path: str
    parsed: ParsedCtg = None
    page: Page = None
    # (``dirty``, the unsaved changes, is a property of the history: doc/history.py)
    # the file could not be written when it was opened (read-only attribute,
    # os.access W_OK): the editor saves such a page with Save As
    read_only: bool = False
    _undo: List[Command] = field(default_factory=list)
    #: what the editor window keeps about this document (DocumentUiState)
    ui: DocumentUiState = field(default_factory=DocumentUiState, repr=False)
    _redo: List[Command] = field(default_factory=list)

    _project_cache: object = None

    # what was wrong with the file when it was opened (format/integrity): the
    # editor warns, and asks before it overwrites the damaged file in place
    damage: tuple = ()
    # the identity of the page file the document was read from (or last saved to):
    # editing_base.file_identity; a batch save refuses a page that changed since
    _open_identity = None
    # the last save wrote the page but not yet its project database rows (io/pendingrows)
    rows_pending = False

    # -- lifecycle -----------------------------------------------------------
    @classmethod
    def open(cls, path: str, before_derive=None) -> "EditableDocument":
        """The page at ``path``. ``before_derive(doc)`` = a step on the records before the
        open's one derive (the document holds only its record forest then; True = it changed
        them): the editor applies a remembered genie update there, which saves a second
        derive after the page shows."""
        from .format import integrity
        from .format.page import parse_ctg_bytes

        # (the identity of the file the bytes came from: a batch save checks it is still
        # that file before it writes)
        data, identity = read_with_identity(path)
        integrity.check_openable(path, data)
        doc = cls(path=path)
        doc._open_identity = identity
        try:
            doc.parsed = parse_ctg_bytes(data)
        except (struct.error, IndexError, UnicodeDecodeError, ValueError) as e:
            raise integrity.DamagedFileError(
                "%s could not be opened: the file is damaged or truncated (%s). Restore it "
                "from a backup." % (os.path.basename(path), e)
            ) from e
        if doc.parsed.page is None:
            if os.path.splitext(path)[1].lower() in (".ctl", ".ctm", ".ctt"):
                raise ValueError(
                    "%s is a library (symbols, genies or templates), not a page." % os.path.basename(path)
                )
            if any(b.offset + b.size > len(data) for b in doc.parsed.container.blocks):
                raise integrity.DamagedFileError(
                    "%s could not be opened: the file is truncated (it ends before its "
                    "page data, %d bytes are left). Restore it from a backup."
                    % (os.path.basename(path), len(data))
                )
            raise ValueError(
                "%s is not a graphics page, or it is damaged (it has no page data)." % os.path.basename(path)
            )
        doc.damage = tuple(integrity.problems(doc.parsed, len(data)))
        fresh = True
        if before_derive is not None and not doc.damage:
            doc.read_only = cls.file_read_only(path)
            doc._open_sha1 = hashlib.sha1(data).digest()
            doc._records_only = True
            try:
                doc._derive(fresh=True)
                fresh = not before_derive(doc)
            except Exception:  # noqa: BLE001 -- the open goes on (the step is optional)
                from .infra import logsetup

                logsetup.note_exception("editing.open/before_derive", page=path)
                fresh = not doc._undo
            finally:
                doc._records_only = False
        try:
            # derived from the records just parsed (no second parse of the file)
            doc._derive(fresh=fresh)
        except Exception as e:  # noqa: BLE001 -- say it is the damage, not a bug
            if not doc.damage:
                raise
            raise integrity.DamagedFileError(
                "%s is damaged (%s) and cannot be shown (%s: %s). Restore it from a "
                "backup." % (os.path.basename(path), "; ".join(doc.damage), type(e).__name__, e)
            ) from e
        doc._disk_stamp = doc.disk_stamp(path)
        doc.read_only = cls.file_read_only(path)
        doc._open_sha1 = hashlib.sha1(data).digest()  # (genie_noop_key)
        doc._composite_links_opened(data)
        doc._start_ctf_precheck(data)
        return doc

    @classmethod
    def from_bytes(
        cls, data: bytes, path: str, records_only: bool = False, lazy_bags: bool = False, identity=None
    ) -> "EditableDocument":
        """A document holding ``data`` (e.g. a recovery copy) that belongs to ``path``."""
        from .format.page import parse_ctg_bytes

        doc = cls(path=path)
        doc._open_identity = identity
        doc.parsed = parse_ctg_bytes(bytes(data), lazy=lazy_bags)
        if doc.parsed.page is None:
            raise ValueError("%s: the data holds no page" % os.path.basename(path))
        doc._records_only = bool(records_only)
        doc._derive(fresh=True)
        doc._disk_stamp = doc.disk_stamp(path)
        doc.read_only = cls.file_read_only(path)
        doc._open_sha1 = hashlib.sha1(data).digest()  # (genie_noop_key)
        doc._composite_links_opened(data)
        return doc

    @staticmethod
    def file_read_only(path: str) -> bool:
        """An existing file this user cannot write (on Windows: the read-only
        attribute). A file locked by another program is not detected here --
        its save fails and says so."""
        return os.path.exists(path) and not os.access(path, os.W_OK)

    def _start_ctf_precheck(self, data: Optional[bytes] = None) -> None:
        """Start the fast-display check for this page as it is on disk."""
        self._ctf_pre = None
        if os.environ.get("CTG_NO_CTF_PRECHECK") == "1":
            return
        path = self.path
        pdir = os.path.dirname(os.path.abspath(path))
        if not is_project_dir(pdir):
            return
        ctf = path[:-1] + "F" if path.lower().endswith(".ctg") else None
        if not ctf or not os.path.exists(ctf):
            return
        try:
            if data is None:
                with open(path, "rb") as fh:
                    data = fh.read()
            with open(ctf, "rb") as fh:
                ctf_b = fh.read()
        except OSError:
            return
        # the bytes the verdict is about, by their sha1 (not kept: a page and
        # its .ctF are megabytes)
        box = {"ctg": sha1(data), "ctf": sha1(ctf_b), "ok": None}
        box["thread"] = CtfPrecheck(box, path, resolver=self._resolver)
        self._ctf_pre = box

    def start_ctf_precheck(self) -> bool:
        """Start the prepared trust check's process (the editor calls it when
        idle). False = another check process is running: call again later;
        True = started, finished, or nothing to check."""
        box = getattr(self, "_ctf_pre", None)
        if not box:
            return True
        return box["thread"].start()

    def ctf_precheck_pending(self) -> bool:
        """A prepared trust check whose process has not been started yet."""
        box = getattr(self, "_ctf_pre", None)
        return bool(box) and box.get("ok") is None and not box["thread"].started()

    def _settle_ctf_precheck(self) -> None:
        """Wait for a RUNNING check process of this page (a save is about to
        replace the files it reads); its verdict stays for the save."""
        box = getattr(self, "_ctf_pre", None)
        if box and box["thread"].is_alive():
            box["thread"].join()

    def stop_ctf_precheck(self) -> None:
        """The document is closed: stop its check process, forget the check."""
        box = getattr(self, "_ctf_pre", None)
        self._ctf_pre = None
        if box:
            box["thread"].cancel()

    def _ctf_precheck_result(self, previous: bytes, old_ctf: bytes):
        """The trust check's verdict when it is about exactly these bytes
        (waits for a running check process; computes it when the process was
        never started or failed), else None."""
        box = getattr(self, "_ctf_pre", None)
        if not box or box["ctg"] != sha1(previous) or box["ctf"] != sha1(old_ctf):
            return None
        return box["thread"].join()

    @staticmethod
    def disk_stamp(path: str):
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def changed_on_disk(self) -> bool:
        """True when the page file was rewritten by something else (CtDraw,
        another editor) since it was opened / last saved here."""
        stamp = getattr(self, "_disk_stamp", None)
        return stamp is not None and self.disk_stamp(self.path) not in (None, stamp)

    def adopt_disk_version(self) -> None:
        """The page file as it is now is this tab's baseline (what 'changed on disk' and a
        batch save compare with); the records, the undo history and the unsaved changes stay.
        For a file the editor's own helper wrote from the version this tab read (the chain
        after a genie save: what it changed, the tab changes itself)."""
        self._disk_stamp = self.disk_stamp(self.path)
        self._open_identity = file_identity(self.path)

    @classmethod
    def new_blank(cls, template_path: str, new_path: str, register: bool = True) -> "EditableDocument":
        """A blank page from a template, written and opened."""
        stem = os.path.splitext(os.path.basename(new_path))[0]
        tdir = os.path.dirname(os.path.abspath(new_path))
        check_page_name(stem, tdir)
        parsed = parse_ctg(template_path)
        if parsed.page is not None:
            # keep the page TEMPLATE (the .ctt placement + its art-stripped
            # copy of the template stream): a page without it has no chrome
            # and no template inputs
            src = cls.open(template_path)
            tpl_i, _ref = src.template_reference()
            keep = src.subtree(tpl_i) if tpl_i is not None else []
            parsed.page.objects[:] = [parsed.page.objects[i] for i in keep]
            parsed.page.object_count = len(parsed.page.objects)
            parsed.page.thumbnail = b""
            prune_link_table(parsed.page)
        fresh_identity(parsed, created=True)
        data = writer.serialize_ctg(parsed.container, parsed.page, parsed.attributes)
        write_atomic(new_path, data)
        doc = cls.open(new_path)
        if register:
            # the save that registers it creates the page: its 'created' stamp is that
            # save's, as with CtDraw's first save of a new page
            doc._creating = True
            doc.save()
        return doc

    @classmethod
    def new_from_template(
        cls,
        template_ref: str,
        new_path: str,
        *,
        linked: bool = True,
        background=None,
        register: bool = True,
        write: bool = True,
    ) -> "EditableDocument":
        """File > New > Page from a template."""
        from .format import template_place as TP
        from .format.symbols import SymbolResolver

        stem = os.path.splitext(os.path.basename(new_path))[0]
        guard_text(stem, template_ref)
        if not stem:
            raise ValueError("Enter a name for the new page.")
        pdir = os.path.dirname(os.path.abspath(new_path))
        check_page_name(stem, pdir)
        if os.path.exists(new_path):
            raise FileExistsError("%s already exists. Choose another page name." % os.path.basename(new_path))
        extra = lib_path_dirs()
        res = SymbolResolver.for_page(new_path, extra_paths=extra)
        tpl = TP.resolve_template(res, TP.reference_for(template_ref))
        if tpl is None:
            raise KeyError(
                "Template %s is not in project %s or the projects it includes."
                % (template_ref, os.path.basename(pdir))
            )
        data = TP.new_page(
            tpl, template_ref, page_name=stem, linked=linked, background=background, resolver=res
        )
        if not write:
            doc = cls.from_bytes(data, new_path)
            doc.dirty = True
            return doc
        write_atomic(new_path, data)
        doc = cls.open(new_path)
        if register:
            # the save that registers it creates the page: its 'created' stamp is that
            # save's, as with CtDraw's first save of a new page
            doc._creating = True
            doc.save()
        return doc

    def project(self):
        """The Citect project this page lives in (for tag autocomplete, etc.)."""
        if self._project_cache is None:
            from .io.project import Project

            self._project_cache = Project(os.path.dirname(self.path))
        return self._project_cache

    def tag_index(self):
        """The names of the page's tag scope (io.project.tag_index: its project,
        the projects it includes and the projects compiled with it)."""
        from .io import project as PR

        try:
            return PR.tag_index(self._project_dir())
        except Exception:  # noqa: BLE001 -- the lists are an aid, never required
            _note_exc("editing.EditableDocument.tag_index")
            return PR.TagIndex(())

    def tag_names(self) -> list[str]:
        """The variable tags in the page's tag scope, sorted, each once."""
        from .io import project as PR

        try:
            pdir = self._project_dir()
            idx = PR.cached_tag_index(pdir)
            if idx is None:
                idx = PR.tag_index(pdir, near_only=True)
            return list(idx.sorted_names(("variable",)))
        except Exception:  # noqa: BLE001
            _note_exc("editing.EditableDocument.tag_names")
            return []

    def tag_entry(self, name: str):
        """The TagEntry of a variable tag, local variable or Equip.Item reference
        of the page's tag scope (with its project and equipment), or None."""
        try:
            return self.tag_index().get(name)
        except Exception:  # noqa: BLE001
            _note_exc("editing.EditableDocument.tag_entry", name=name)
            return None

    def tag_format(self, name: str) -> Optional[str]:
        """A variable tag's display FORMAT ('' when it has none), None when the
        tag scope does not define the tag (reader.tag_format_lookup: the lookup
        the reader measures with)."""
        fm = getattr(self, "_tag_formats", None)
        if fm is None:
            try:
                from .io.reader import tag_format_lookup

                fm = tag_format_lookup(os.path.abspath(self.path or "page.ctg"))
            except Exception:  # noqa: BLE001
                _note_exc("editing.EditableDocument.tag_format", path=self.path)
                fm = None
            self._tag_formats = fm if fm is not None else {}
            fm = self._tag_formats
        return fm.get(name.strip().lower())

    def _use_tag_formats(self) -> None:
        from .format import colourstate, textmetrics

        # the lookup is found again once per edit (a table may have changed)
        self._tag_formats = None
        textmetrics.use_tag_formats(self.tag_format)
        # the colour-array defaults a new COL_ARR section takes
        colourstate.use_palette(self.colour_palette())

    def colour_palette(self):
        """The project's colour-number table (256 (on, off) pairs: its
        citect.pal, format.colourpalette) -- the defaults of a Fill > Color
        array entry. Read once per project folder and palette file version
        (Edit Favorite Colors rewrites the file)."""
        from .format import colourpalette

        pdir = os.path.dirname(os.path.abspath(self.path or ""))
        # (the file is found once per folder and palette generation -- a save
        # of the favourites bumps it -- then only stat'ed: this runs per edit)
        where = self.__dict__.get("_colour_palette_file")
        if where is None or where[0] != (pdir, colourpalette.GENERATION):
            where = self._colour_palette_file = (
                (pdir, colourpalette.GENERATION),
                colourpalette.palette_file(pdir),
            )
        try:
            st = os.stat(where[1]) if where[1] else None
            key = (where[0], where[1], st.st_mtime if st else None, st.st_size if st else None)
        except OSError:
            key = (where[0], None)
        cached = self.__dict__.get("_colour_palette")
        if cached is None or cached[0] != key:
            try:
                table = colourpalette.table_for_project(pdir)
            except Exception:  # noqa: BLE001 -- no palette: the classic colours
                _note_exc("editing.EditableDocument.colour_palette", project=pdir)
                table = colourpalette.standard_table()
            cached = self._colour_palette = (key, table)
        return cached[1]

    # -- helpers ---------------------------------------------------------------
    @property
    def objects(self):
        return self.parsed.page.objects if self.parsed and self.parsed.page else []

    def _apply(self, label: str, do: Callable[[], None], undo: Callable[[], None]) -> None:
        snap = self._edit_snapshot()
        try:
            do()
        except Exception:
            # an edit that fails half-way must not leave the page half
            # changed with no undo entry (the Undo stack would not match it)
            self._restore_edit_snapshot(snap)
            raise
        self._push_step(Command(label, do, undo))
        self._note_edit()

    #: counts the edits, Undos and Redos of the page, also those that change
    #: no record (page colour, page size, page properties, associations):
    #: what a page window (Page Properties) checks besides derive_serial
    edit_serial = 0

    def _note_edit(self) -> None:
        self.edit_serial = self.edit_serial + 1
        self._compact_states()
        self._trim_undo()
        _UNDO_DOCS.add(self)
        _trim_undo_everywhere(self)

    def _trim_undo(self) -> None:
        """Drop the oldest undo steps past _undo_page_cap() (the newest UNDO_KEEP_MIN stay):
        one page keeps at most two thirds of Settings > Editing > Undo memory."""
        undo = self._undo
        if len(undo) <= UNDO_KEEP_MIN or getattr(self, "_batching", 0):
            return
        floor = getattr(self, "_mark_floor", None)
        # steps from index 'lowest' on are made after an open history mark: kept
        lowest = len(undo) if floor is None else floor[0] - (getattr(self, "undo_trimmed", 0) - floor[1])
        total = 0
        cap = _undo_page_cap()
        for k in range(len(undo) - 1, -1, -1):
            total += command_bytes(undo[k])
            if total > cap and len(undo) - k > UNDO_KEEP_MIN and k < lowest:
                self._saved_point_trimmed(undo[: k + 1])
                del undo[: k + 1]
                self.undo_trimmed = getattr(self, "undo_trimmed", 0) + k + 1
                return

    def _edit_snapshot(self):
        """Enough of the document to put it back exactly after a failed edit:
        the record list and each record's state (bytes copied), the page /
        attributes block fields, the rendered page."""
        pb = self.parsed.page if self.parsed is not None else None
        if pb is None:
            return None

        def state(o):
            # a C-level copy; only the mutable values are copied again (bags and text_exprs
            # are lists -- never changed in place, copied anyway -- and a bytearray body)
            d = o.__dict__.copy()
            for k in ("bags", "text_exprs"):
                v = d.get(k)
                if type(v) is list:
                    d[k] = v.copy()
            v = d.get("body_raw")
            if type(v) is bytearray:
                d["body_raw"] = bytearray(v)
            return d

        import copy

        objs = list(pb.objects)
        at = self.parsed.attributes
        return (
            objs,
            [state(o) for o in objs],
            {k: v for k, v in pb.__dict__.items() if k != "objects"},
            copy.deepcopy(at.__dict__) if at is not None else None,
            self.page,
        )

    def _restore_edit_snapshot(self, snap) -> None:
        if snap is None:
            return
        objs, states, page_fields, attr_state, page = snap
        pb = self.parsed.page
        pb.objects[:] = objs
        for o, st in zip(objs, states):
            o.__dict__.clear()
            o.__dict__.update(st)
        for k, v in page_fields.items():
            setattr(pb, k, v)
        if attr_state is not None and self.parsed.attributes is not None:
            self.parsed.attributes.__dict__.clear()
            self.parsed.attributes.__dict__.update(attr_state)
        if self.page is not page or getattr(self, "_touched", None):
            # the failed edit re-derived (the old model shares the objects the
            # derive reused) or shifted the model in place: derive the model of
            # the restored records instead of putting the old one back; when
            # the incremental derive fails, derive everything afresh (no object
            # shared with the failed state)
            for _attempt in range(2):
                try:
                    self._derive()
                    return
                except Exception:  # noqa: BLE001 -- keep the old model then
                    _note_exc("editing.EditableDocument._restore_edit_snapshot")
                    self._derivation = None
            # a new model for the views: they rebuild it all
            self.derive_serial = getattr(self, "derive_serial", 0) + 1
            self.__dict__.setdefault("_derive_log", []).append((self.derive_serial, None))
        self.page = page

    @contextlib.contextmanager
    def batch(self, label: str):
        """Group every edit made inside the block into ONE undo step (and one
        page re-render instead of one per object): multi-selection property
        edits, bulk recolours."""
        start = len(self._undo)
        self._batching = getattr(self, "_batching", 0) + 1
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._batching -= 1
            if self._batching == 0:
                cmds = self._undo[start:]
                del self._undo[start:]
                if failed and cmds:
                    # all or nothing: an edit that fails part way (a position the page
                    # cannot store, the third of five objects) takes back the steps it
                    # made, so no half-made edit stays on the page
                    try:
                        with self._quiet():
                            for c in reversed(cmds):
                                c.undo()
                    except Exception:  # noqa: BLE001 -- the first error is the one raised
                        from .infra import logsetup

                        logsetup.note_exception("editing.batch.rollback")
                    self._rerender_pending = True
                    cmds = []
                if cmds:

                    def do():
                        with self._quiet():
                            for c in cmds:
                                c.do()

                    def undo():
                        with self._quiet():
                            for c in reversed(cmds):
                                c.undo()

                    one = Command(label, do, undo)
                    self._undo.append(one)
                    self._saved_point_merged(cmds, one)
                if getattr(self, "_rerender_pending", False):
                    self._rerender_pending = False
                    self._rerender_page()

    def merge_last_steps(self, n: int = 2):
        """Merge the last ``n`` undo steps into one."""
        if n < 2 or len(self._undo) < n or self._redo:
            return None
        saved, depth = self.saved_depth, self.history_depth
        if saved is not None and depth - n < saved < depth:
            return None
        cmds = self._undo[-n:]
        del self._undo[-n:]

        def do():
            with self._quiet():
                for c in cmds:
                    c.do()

        def undo():
            with self._quiet():
                for c in reversed(cmds):
                    c.undo()

        # (automatic steps made one stay automatic: no step of the user's, for the update
        # after a genie save, library._tab_clean, and for the steps to redo)
        one = Command(
            cmds[0].label,
            do,
            undo,
            when=cmds[0].when,
            targets=cmds[0].targets,
            automatic=all(getattr(c, "automatic", False) for c in cmds),
        )
        self._undo.append(one)
        self._saved_point_merged(cmds, one)
        return one

    def fold_last_step(self) -> bool:
        """Fold the last undo step into the one before it."""
        if len(self._undo) < 2 or self._redo:
            return False
        saved, depth = self.saved_depth, self.history_depth
        if saved is not None and saved == depth - 1:
            return False
        first, second = self._undo[-2], self._undo[-1]
        do1, undo1, do2, undo2 = first.do, first.undo, second.do, second.undo

        def do():
            with self._quiet():
                do1()
                do2()

        def undo():
            with self._quiet():
                undo2()
                undo1()

        first.do, first.undo = do, undo
        first.nbytes = -1  # (measured again: command_bytes)
        first.folded = int(getattr(first, "folded", 0) or 0) + 1
        del self._undo[-1]
        self._saved_point_merged([first, second], first)
        return True

    @contextlib.contextmanager
    def _quiet(self):
        """Suppress per-command page re-renders; re-render once at the end."""
        self._batching = getattr(self, "_batching", 0) + 1
        try:
            yield
        finally:
            self._batching -= 1
            if self._batching == 0 and getattr(self, "_rerender_pending", False):
                self._rerender_pending = False
                self._rerender_page()

    def _rerender_page(self) -> None:
        """Re-derive the semantic page from the current records (picks up symbol
        rendering automatically). In memory, and only the root units whose
        records changed since the last derive (reader.derive_page) -- nothing
        is written to %TEMP% and no environment variable is changed."""
        if getattr(self, "_batching", 0):
            self._rerender_pending = True
            return
        self._derive()

    #: counts the derives of :meth:`rerender_for_libraries` (part of derive_serial): what
    #: the Simulate window leaves out of 'the page changed in the editor'
    library_derives = 0

    def rerender_for_libraries(self) -> None:
        """Re-render because library files the page reads changed."""
        clean = not (
            getattr(self, "_batching", 0)
            or getattr(self, "_rerender_pending", False)
            or getattr(self, "_touched", None)
        )
        before = getattr(self, "derive_serial", 0)
        self._rerender_page()
        if clean and getattr(self, "derive_serial", 0) == before + 1:
            self.library_derives = self.library_derives + 1

    #: compare every incremental derive with a full one and raise on a
    #: difference (the debug check; also CTG_DERIVE_CHECK=1)
    derive_check = False

    def _derive(self, fresh: bool = False) -> None:
        """``self.page`` from ``self.parsed``. ``fresh`` = the records were just
        parsed (open): the reader reads them as they are."""
        if getattr(self, "_records_only", False):
            self.page = RecordForest(
                self.parsed.page.objects, os.path.splitext(os.path.basename(self.path))[0]
            )
            self._touched = set()
            self._touch_log = []
            self.derive_serial = getattr(self, "derive_serial", 0) + 1
            return
        from .io.reader import derive_page

        prev = getattr(self, "_derivation", None)
        if prev is not None and self.page is not prev.page:
            prev = None  # the model was replaced: derive it all
        touched = getattr(self, "_touched", None) or ()
        from time import perf_counter

        t0 = perf_counter()  # (the open's log line reports it)
        page, der = derive_page(
            self.parsed,
            self.path,
            prev,
            records_fresh=fresh,
            generation=LIBRARY_GENERATION[0],
            touched=touched,
        )
        self._derive_seconds = perf_counter() - t0
        self._touched = set()
        self._touch_log = []
        self._derivation = der
        self.page = page
        self.derive_serial = getattr(self, "derive_serial", 0) + 1
        log = self.__dict__.setdefault("_derive_log", [])
        log.append((self.derive_serial, None if der.full else frozenset(der.changed_roots)))
        del log[:-32]
        kept = self.__dict__.setdefault("_derive_kept", {})
        kept[self.derive_serial] = getattr(der, "kept", None) or {}
        for s in [s for s in kept if s <= self.derive_serial - 32]:
            del kept[s]
        if self.derive_check or os.environ.get("CTG_DERIVE_CHECK") == "1":
            self._check_derive()

    def _check_derive(self) -> None:
        """The debug check: the incremental model equals a full derive of the
        page as it would be saved now."""
        from .format.page import parse_ctg_bytes
        from .io.reader import derive_page, model_difference

        full, _der = derive_page(
            parse_ctg_bytes(self.serialize()),
            self.path,
            records_fresh=True,
            generation=LIBRARY_GENERATION[0],
        )
        diff = model_difference(self.page, full)
        if diff:
            raise AssertionError(
                "the incremental re-derive of %s differs from a full "
                "one: %s" % (os.path.basename(self.path), diff)
            )

    def _touch_model(self, indices, move=None) -> None:
        """Mark objects changed in place so the next derive picks them up."""
        objs = self.parsed.page.objects if self.parsed and self.parsed.page else []
        t = self.__dict__.setdefault("_touched", set())
        ids = set()
        for i in indices:
            if 0 <= i < len(objs):
                t.add(id(objs[i]))
                ids.add(id(objs[i]))
        self.touch_serial = getattr(self, "touch_serial", 0) + 1
        self.__dict__.setdefault("_touch_log", []).append((self.touch_serial, ids, move))

    #: counts the in-place model changes (_touch_model)
    touch_serial = 0

    def pending_moves(self, after: int):
        """The in-place changes since touch ``after`` as [(record indices, dx, dy)] when
        every one of them is a pure move (None: something else changed in place, or the
        model was derived since -- a view rebuilds then)."""
        log = getattr(self, "_touch_log", None) or []
        ents = [e for e in log if e[0] > after]
        if not ents or any(m is None for _s, _ids, m in ents):
            return None if ents else []
        pos = {id(po): i for i, po in enumerate(self.parsed.page.objects)}
        out = []
        for _s, ids, (dx, dy) in ents:
            out.append((sorted(pos[x] for x in ids if x in pos), dx, dy))
        return out

    def model_changes_since(self, serial, touches_after: Optional[int] = None) -> Optional[set]:
        """Root indices whose model objects changed since derive ``serial``."""
        if serial is None or self.page is None:
            return None
        cur = getattr(self, "derive_serial", 0)
        log = getattr(self, "_derive_log", [])
        out: set = set()
        if serial != cur:
            steps = [(s, ch) for s, ch in log if s > serial]
            if len(steps) != cur - serial:
                return None  # older than the log
            for _s, ch in steps:
                if ch is None:
                    return None
                out |= ch
        touched = getattr(self, "_touched", None)
        if touched and touches_after is not None:
            # (the view already shows the in-place changes up to touch 'touches_after')
            touched = set()
            for s, ids, _m in getattr(self, "_touch_log", None) or ():
                if s > touches_after:
                    touched |= ids
        if touched:
            objs = self.parsed.page.objects
            for i, po in enumerate(objs):
                if id(po) in touched:
                    out.add(self.page.root_of(i) if i < len(self.page.objects) else i)
        return out

    def model_moves_since(self, serial, changes, touches_after: Optional[int] = None) -> dict:
        """{root index now: index at derive ``serial``} for roots that only moved."""
        cur = getattr(self, "derive_serial", 0)
        if serial is None or self.page is None or not changes or serial >= cur:
            return {}
        kept_log = getattr(self, "_derive_kept", None) or {}
        moves = None
        for s in range(serial + 1, cur + 1):
            step = kept_log.get(s)
            if not step:
                return {}
            # (root at derive s -> root at derive ``serial``)
            moves = dict(step) if moves is None else {r: moves[o] for r, o in step.items() if o in moves}
        if not any(r in moves for r in changes):
            return {}  # (an edit in place: nothing moved)
        touched = getattr(self, "_touched", None)
        if touched and touches_after is not None:
            touched = set()
            for s, ids, _m in getattr(self, "_touch_log", None) or ():
                if s > touches_after:
                    touched |= ids
        if touched:
            page = self.page
            for i, po in enumerate(self.parsed.page.objects):
                if id(po) in touched and i < len(page.objects):
                    moves.pop(page.root_of(i), None)
        roots = self.page.roots()
        fixed = [r for r in roots if r not in changes]
        cand = [
            (r, moves[r])
            for r in roots
            if r in changes
            and r in moves
            and bisect.bisect_left(fixed, r) == bisect.bisect_left(fixed, moves[r])
        ]
        return dict(_rising_run(cand))

    def _body_edit(self, label: str, index: int, mutate: Callable[[PageObject], object]):
        """Generic single-object body edit with snapshot undo/redo."""
        objs = self.parsed.page.objects
        po = objs[index]
        self._use_tag_formats()
        old = bytes(po.body_raw)
        old_fl = po.subtype_size
        fit = self._fitting_ancestors([index])
        anc_old = {a: bytes(objs[a].body_raw) for a in fit}
        # the members' dwFlags: a bag edit on a container re-derives the 0x10
        # its members inherit (_after_bag_edit)
        members = self._subtree_span(index)[1:]
        mem_old = [objs[j].subtype_size for j in members]
        try:
            result = mutate(po)
            if result is not False and po.type_code == 0x05 and bytes(po.body_raw) != old:
                # the engine re-measures a text on load: write its extent
                if geom.refit_text(po):
                    writer.refresh_object_bags(po)
        except Exception:
            # a mutator that fails half-way must not leave the record
            # half-changed with no undo entry (like _multi_body_edit)
            po.body_raw = old
            po.subtype_size = old_fl
            writer.refresh_object_bags(po)
            raise
        if result is False:
            # a declined edit leaves the record as it was
            if bytes(po.body_raw) != old or po.subtype_size != old_fl:
                po.body_raw = old
                po.subtype_size = old_fl
                writer.refresh_object_bags(po)
            return False
        new = bytes(po.body_raw)
        new_fl = po.subtype_size
        mem_new = [objs[j].subtype_size for j in members]
        if new == old and new_fl == old_fl and mem_new == mem_old:
            return True
        for a in fit:
            self._refit_container(a)
        anc_new = {a: bytes(objs[a].body_raw) for a in fit}

        def put_members(flags):
            for j, fl in zip(members, flags):
                self.parsed.page.objects[j].subtype_size = fl

        def do():
            po.body_raw = new
            po.subtype_size = new_fl
            writer.refresh_object_bags(po)
            put_members(mem_new)
            for a, b in anc_new.items():
                self.parsed.page.objects[a].body_raw = b
            self._rerender_page()

        def undo():
            po.body_raw = old
            po.subtype_size = old_fl
            writer.refresh_object_bags(po)
            put_members(mem_old)
            for a, b in anc_old.items():
                self.parsed.page.objects[a].body_raw = b
            self._rerender_page()

        # mutate() already applied the change; register without re-applying
        self._push_step(Command(label, do, undo))
        self._note_edit()
        self._rerender_page()
        return True


_NO_ELEMENT = object()


#: an element's library no longer stores the block a document was opened from (a worker's
#: nested-genie update of a tab: genie_worker.compute_element; a save that must not write
#: over another one: ElementDocument.save(expect_stored=True))
from .format.element import ElementChangedMeanwhile  # noqa: E402,F401

#: one read of each library per composition (an element's save, its nested update)
from .format.symbols import one_read  # noqa: E402


class page_save_thumbnails:
    """Context manager: page saves in the block take their thumbnail from ``hook(doc)``."""

    def __init__(self, hook=None):
        self.hook = hook
        self._set = False

    def __enter__(self):
        from .doc import history as _history

        if self.hook is not None and _history.SAVE_THUMBNAIL is None:
            _history.SAVE_THUMBNAIL = self.hook
            self._set = True
        return self

    def __exit__(self, *exc):
        from .doc import history as _history

        if self._set and _history.SAVE_THUMBNAIL is self.hook:
            _history.SAVE_THUMBNAIL = None
        self._set = False
        return False


class ElementDocument(EditableDocument):
    """A library element (symbol, genie, super genie or template) opened for editing."""

    # its save patches the session records in place with the stored, normalised element
    # (save: po.__dict__.update): a record's body may change length, so an undo step keeps
    # whole values, not changed blocks
    BLOCK_DIFFS = False

    session = None  # ElementSession (None: a new element, not saved yet)
    element_ref = None  # (library path, element name, kind)
    _COPY_RELINK = False  # the save re-derives the values + import table

    def _refit_button(self, po: PageObject) -> bool:
        """An element button without a WIDTH stays auto-width: the element
        load re-sizes it to its caption (genie_norm._button)."""
        return geom.refit_button(po)

    @property
    def hotspot(self) -> Optional[Tuple[int, int]]:
        """The element's origin e -- where a paste lands, CtDraw's hotspot
        marker in the element window (a moved, not yet saved one included).
        None for a new, unsaved element whose hot spot was never set (its
        first save puts it on the content centre)."""
        if getattr(self, "_hotspot", None) is not None:
            return tuple(self._hotspot)
        if self.session is None:
            return None
        o = getattr(self.session, "origin", None)
        return (int(o[0]), int(o[1])) if o else None

    @property
    def can_set_hotspot(self) -> bool:
        """Only a symbol or genie has a hot spot to move (CtDraw's hot spot
        drag and LibraryObjectHotspotPut need an element window of type 2 /
        8); a template or super genie is a page whose origin is
        its top-left corner."""
        return self.kind in ("symbol", "genie")

    def set_hotspot(self, x: int, y: int) -> bool:
        """Hot Spot Location: set the element's hot spot."""
        if not self.can_set_hotspot:
            raise ValueError(
                "A %s has no hot spot: its reference point is its page's "
                "top-left corner." % {"supergenie": "super genie"}.get(self.kind, self.kind)
            )
        x, y = int(x), int(y)
        if not (-32768 <= x <= 32767 and -32768 <= y <= 32767):
            raise ValueError("The hot spot must lie within -32768 .. 32767 on both axes.")
        old = self.hotspot
        if old is not None and tuple(old) == (x, y):
            return False
        # undo puts back the EXPLICIT old point (a save in between makes the
        # moved point the stored origin); a new element's None = its centre
        prev = old if self.session is not None else getattr(self, "_hotspot", None)

        def put(v):
            self._hotspot = v

        self._apply("hot spot", lambda: put((x, y)), lambda: put(prev))
        return True

    _page_size = None  # a NEW template / super genie: its page size
    _template_edited: set = frozenset()

    # -- lifecycle -----------------------------------------------------------
    @classmethod
    def open_element(cls, lib_path: str, element: str) -> "ElementDocument":
        from .format.element_session import open_element_session

        sess, parsed = open_element_session(lib_path, element)
        doc = cls(path=lib_path)
        doc.session = sess
        doc.parsed = parsed
        doc.element_ref = (lib_path, sess.pe.name, sess.pe.kind)
        doc._disk_stamp = doc.disk_stamp(lib_path)
        doc.read_only = cls.file_read_only(lib_path)
        doc._template_edited = set()
        # the records come straight from the session wrapper's parse: derive
        # them as they are (as EditableDocument.open does), not re-parsed
        doc._derive(fresh=True)
        return doc

    @classmethod
    def new_element(
        cls,
        lib_path: str,
        element: str,
        kind: str,
        *,
        canvas: Optional[Tuple[int, int]] = None,
        page_size: Optional[Tuple[int, int]] = None,
        page_properties=(),
        associations=(),
        background=None,
    ) -> "ElementDocument":
        """File > New Symbol / Genie / Super Genie / Template: an EMPTY,
        unsaved element. Nothing is written until the first Save (an element
        needs at least one object -- CtDraw's loader reads one tree)."""
        from .format.element_session import blank_session

        guard_text(element)
        if not element or len(element.encode(CODEC, "replace")) > 31:
            raise ValueError("Library element names have 1 to 31 characters.")
        if kind == "supergenie" and not element.lower().endswith("@super"):
            element += "@SUPER"
        size = page_size or canvas or (800, 600)
        parsed = blank_session(
            size, page_properties=page_properties, associations=associations, background=background
        )
        doc = cls(path=lib_path)
        doc.parsed = parsed
        doc.element_ref = (lib_path, element, kind)
        doc._page_size = page_size if kind in ("template", "supergenie") else None
        doc._disk_stamp = None
        doc._template_edited = set()
        doc._rerender_page()
        return doc

    # -- identity --------------------------------------------------------------
    @property
    def library_name(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def element_name(self) -> str:
        return self.element_ref[1]

    @property
    def kind(self) -> str:
        return self.element_ref[2]

    @property
    def composites_allowed(self) -> bool:
        """Only a template or a super genie takes a composite genie (help
        1172148_1: never a genie or a symbol)."""
        return self.kind in ("template", "supergenie")

    @property
    def display_name(self) -> str:
        return "%s.%s" % (self.library_name, self.element_name)

    @property
    def is_new(self) -> bool:
        return self.session is None

    @property
    def page_sized(self) -> bool:
        """Templates and super genies are PAGES (their size is edited in Page
        Properties); symbols / genies take the size of their content."""
        return self.kind in ("template", "supergenie")

    def _derive(self, fresh: bool = False) -> None:
        # every derive names the page after the element (the failed-edit
        # restore derives too, not only _rerender_page); the reader shows the texts as
        # CtDraw's element window does (readerparts.common.get_element_window)
        from .io.readerparts.common import derive_options

        with derive_options(element_window=True):
            super()._derive(fresh)
        if self.page is not None and self.element_ref is not None:
            self.page.name = self.element_name
            self.page.source_path = self.path

    def changed_on_disk(self) -> bool:
        """THIS element was rewritten elsewhere since it was opened / saved
        here (saving a SIBLING element changes the library file stamp too, so
        the file stamp is the wrong test)."""
        from .format.element import element_changed_on_disk

        return self.session is not None and element_changed_on_disk(self.session.pe)

    def page_record_available(self) -> bool:
        """A library element has no pages.DBF row / pgkey rows (its page
        properties live in the element itself)."""
        return False

    def _used_handles(self) -> set:
        """The members' handles only. The element window doesn't hold the root group."""
        return super()._used_handles()

    # -- attached super genies (Edit > Attach Super Genies, genie window) -------
    @property
    def can_attach_super_genies(self) -> bool:
        """CtDraw offers Attach Super Genies in the GENIE editor only (window
        flag 8) -- not for a symbol, template or super genie."""
        return self.kind == "genie"

    def _link_entries_now(self):
        from .format import links

        pb = self.parsed.page
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        return (t[0], list(t[1])) if t else ("    ", [])

    def attachments(self) -> List[str]:
        """The super genies this genie attaches ('library.name', stored order)."""
        from .format import links

        return links.super_genie_attachments(self._link_entries_now()[1])

    def set_attachments(self, names: Sequence[str], resolver=None) -> bool:
        """Set the attached super genies (one undo step)."""
        from .format import links

        if not self.can_attach_super_genies:
            raise ValueError(
                "Super genies can only be attached to a genie (this is a %s)."
                % {"supergenie": "super genie"}.get(self.kind, self.kind)
            )
        have = {a.lower() for a in self.attachments()}
        clean = []
        for n in names:
            n = (n or "").strip().split("@", 1)[0]
            if not n:
                continue
            if n.lower() not in have:
                err = self.super_genie_problem(n)
                if err:
                    raise ValueError(err)
                # a new one is stored as the element spells it (CtDraw's picker)
                n = self._super_genie_spelling(n)
            clean.append(n)
        area, entries = self._link_entries_now()
        new = links.set_super_genie_attachments(entries, clean, self._super_resolve(resolver))
        raw, cnt = links.serialize_link_table(area, new)
        pb = self.parsed.page
        if (raw, cnt) == (pb.link_table_raw, pb.link_count) or new == entries:
            return False
        old = (pb.link_table_raw, pb.link_count)

        def apply(state):
            pb.link_table_raw, pb.link_count = state

        self._apply("attach super genies", lambda: apply((raw, cnt)), lambda: apply(old))
        return True

    def _with_attachments(self, content: bytes) -> bytes:
        """A NEW element's content with this document's attached super genies
        appended to its import table (new_element_content derives the table
        from the records alone)."""
        from .format import links
        from .format.container import Block
        from .format.element import PAD
        from .format.page import parse_page

        binds = [
            (g, n)
            for g, n in self._link_entries_now()[1]
            if "@super" in links.strip_stamp(n).lower() and not n.startswith("\\")
        ]
        if not binds:
            return content
        blk = Block(1, "new", 0, len(content) + PAD, content + bytes(PAD))
        pb = parse_page(blk, 0x0C)
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        area, ents = (t[0], list(t[1])) if t else ("    ", [])
        have = {links.strip_stamp(n).lower() for _g, n in ents}
        ents += [(g, n) for g, n in binds if links.strip_stamp(n).lower() not in have]
        pb.link_table_raw, pb.link_count = links.serialize_link_table(area, ents)
        return writer.serialize_page_block(pb, blk.data)[: -writer.PAGE_TRAILING_PAD]

    # -- genie-element property edits -------------------------------------------
    def _tree(self) -> List[PageObject]:
        root = self.session.root if self.session is not None else None
        return ([root] if root is not None else []) + list(self.parsed.page.objects)

    def _dynamic_context(self):
        # the unwrapped element root is the parent its members inherit 0x10
        # from (CtDraw asks a group / symbol-set parent)
        root = self.session.root if self.session is not None else None
        return self._tree(), (1 if root is not None else 0)

    def is_genie_element(self) -> bool:
        """The element declares %parameters% of its own (a genie)."""
        from .format import genie_norm as GN

        try:
            return GN.is_genie_element(self._tree())
        except Exception:  # noqa: BLE001
            _note_exc("editing.ElementDocument.is_genie_element")
            return False

    def _template_record(self, index: int) -> bool:
        """``index`` carries a genie TEMPLATE bag (a genie-element member)."""
        from .format.genie_place import decode

        po = self.parsed.page.objects[index]
        if not (po.subtype_size & 0x8) or not self.is_genie_element():
            return False
        try:
            r = decode(po)
        except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read
            return False
        return bool(r.entries) and len(r.entries) >= 2

    def _inside_nested_genie(self, index: int) -> bool:
        """``index`` is a member of a genie NESTED in this element (an ancestor in the
        session is a nested genie root)."""
        sem = self.page.objects if self.page is not None else []
        objs = self.parsed.page.objects
        a = sem[index].parent if index < len(sem) else None
        while a is not None:
            if objs[a].class_flag and (objs[a].subtype_size & 0x200):
                return True
            a = sem[a].parent
        return False

    def object_properties(self, index: int) -> dict[str, str]:
        if self._template_record(index):
            from .format.genie_place import decode

            return dict(decode(self.parsed.page.objects[index]).entries[0][2].pairs)
        return super().object_properties(index)

    def text_caption(self, index: int) -> Optional[str]:
        """A genie-element text's caption is its TEMPLATE TXT_OFF (the tail is
        re-derived from it on save): None, so the Display Value page reads
        and writes TXT_OFF."""
        if self._template_record(index):
            return None
        return super().text_caption(index)

    def _text_display_per_key(self, index: int) -> bool:
        return self._template_record(index)

    def set_member_property(self, index: int, key: str, value: str) -> bool:
        """Set a member's property the way CtDraw's genie editor does (in the template bag)."""
        from .format import genie_norm as GN

        guard_text(key, value)
        if self._inside_nested_genie(index):
            # CtDraw greys such a member: its bags come from the nested genie's own
            # element (the save merges them back from it, so the edit would be lost)
            raise ValueError(
                "This object belongs to a genie nested in this element. "
                "Edit it in that genie's own library element, or change the "
                "nested genie's parameters."
            )
        # the re-derive below measures texts and fills a new colour array from
        # the project palette (colourstate.array_default)
        self._use_tag_formats()
        objs = self.parsed.page.objects
        po = objs[index]
        old_payload = writer.object_payload(po)
        new_po = GN.set_template_property(po, key, value)
        if writer.object_payload(new_po) == old_payload:
            return False
        return self._rederive_member(index, new_po, key, value)

    def _rederive_member(self, index: int, new_po, key: str = "", value: str = "") -> bool:
        """Put ``new_po`` (member ``index`` with a changed TEMPLATE bag) into the element
        and derive its substituted values, declarations and text again (and the other
        members' when it brings the first %parameter%); one undo step, and the save
        derives it again from its template (``_template_edited``)."""
        from .format import genie_norm as GN

        objs = self.parsed.page.objects
        off = 1 if (self.session is not None and self.session.root is not None) else 0
        tree = self._tree()
        tree[index + off] = new_po
        convert = GN.needs_conversion(self._tree(), value)
        ext = os.path.splitext(self.path)[1].lower().lstrip(".")
        origin = self.session.pe.origin if self.session is not None else (0, 0)
        try:
            normed = GN.normalise_records(tree, ext=ext, convert=convert, edited={index + off}, e=origin)
        except Exception:  # noqa: BLE001 -- display derive only; Save re-derives
            _note_exc("editing.ElementDocument._rederive_member", index=index)
            normed = tree
        changed = {index: normed[index + off]}
        if convert:  # the first %param%: every direct member converts
            changed = {k: normed[k + off] for k in range(len(objs))}
        if key.upper().startswith("METADATA_"):
            # the saver derives the dwFlags-0x100 section from the values
            for p in changed.values():
                writer.rebuild_metadata_section(p)
        before = {k: objs[k] for k in changed}

        def apply(m):
            for k, p in m.items():
                objs[k] = p
            self._rerender_page()

        self._apply("property %s" % key, lambda: apply(changed), lambda: apply(before))
        h = geom.get_handle(new_po)
        if h is not None:
            self._template_edited = set(getattr(self, "_template_edited", set())) | {h}
        return True

    def set_property(self, index: int, key: str, value: str) -> bool:
        if self._template_record(index):
            return self.set_member_property(index, key, value)
        return super().set_property(index, key, value)

    def _input_record_free(self, po) -> bool:
        """A genie's own command record (a member: a template and its values) is
        edited here too (set_input_commands takes what is typed as its template)."""
        if super()._input_record_free(po):
            return True
        if not self.is_genie_element() or not (po.subtype_size & 0x8):
            return False
        from .format.genie_place import decode

        try:
            r = decode(po)
        except Exception:  # noqa: BLE001 -- swallow-ok: an undecodable record stays read-only
            return False
        return bool(r.entries) and len(r.entries) == 2

    def _template_values(self, index: int) -> dict:
        """A genie member's own values as its sheet shows them (its %parameter%
        templates), '!' editor keys left out."""
        return {k: v for k, v in self.object_properties(index).items() if not k.startswith("!")}

    def input_commands(self, index: int) -> dict:
        """In a genie: the touch / keyboard commands as their %parameter% templates
        (a command record is a genie member: its own value bag holds them
        substituted with the defaults, a parameter without one as '')."""
        out = super().input_commands(index)
        if not self.is_genie_element():
            return out
        from .format import inputs

        objs = self.parsed.page.objects
        if out.get("button"):
            if self._template_record(index):
                own = self._template_values(index)
                touch = {k: own[k] for k in inputs.BUTTON_TOUCH_KEYS if own.get(k)}
                out["touch"] = touch or None
        else:
            t = self.touch_child(index)
            if t is not None and self._template_record(t):
                out["touch"] = self._template_values(t)
        out["keys"] = [
            self._template_values(c) if self._template_record(c) else dict(inputs.values(objs[c]))
            for c in self.keyboard_children(index)
        ]
        return out

    def set_input_commands(self, index: int, *, touch=None, keys=None, label: str = "input commands") -> bool:
        """In a genie: an existing command record is a genie member, so what is typed is
        its TEMPLATE (set_member_property's rule: the save derives the substituted
        values and declarations from it); a new one is written as on a page and the
        save makes it a member. One undo step."""
        if not self.is_genie_element():
            return super().set_input_commands(index, touch=touch, keys=keys, label=label)
        from .format import inputs

        cur = self.input_commands(index)

        def same(a, b, names):
            return all(str((a or {}).get(k) or "") == str((b or {}).get(k) or "") for k in names)

        live = inputs.TOUCH_COMMANDS + inputs.TOUCH_LOGS
        touch_same = touch is None or same(touch, cur.get("touch"), live)
        keys_same = keys is None or (
            len([d for d in keys if (d.get("KEY_SEQ") or d.get("CMD"))]) == len(cur["keys"])
            and all(
                same(a, b, inputs.KEYBOARD_KEYS)
                for a, b in zip([d for d in keys if (d.get("KEY_SEQ") or d.get("CMD"))], cur["keys"])
            )
        )
        if touch_same and keys_same:
            return False
        objs = self.parsed.page.objects
        # (the records as they are: only the ones the page rule rewrites take what was
        # typed as their template; an untouched one keeps its own)
        before = {id(p) for p in objs}
        with self.batch(label):
            changed = super().set_input_commands(index, touch=touch, keys=keys, label=label)
            # the members just written: their template = what was typed (the
            # page rule wrote it into the substituted bag)
            owner_is_button = objs[index].type_code == inputs.BUTTON_TC
            targets = (
                [index]
                if owner_is_button and touch is not None
                else [c for c in [self.touch_child(index)] if c is not None and touch is not None]
            ) + (list(self.keyboard_children(index)) if keys is not None else [])
            for c in targets:
                if not self._template_record(c) or id(objs[c]) in before:
                    continue
                vals = (
                    dict(inputs.values(objs[c]))
                    if not owner_is_button
                    else {k: v for k, v in objs[c].raw_properties().items() if k in inputs.BUTTON_TOUCH_KEYS}
                )
                names = (
                    inputs.BUTTON_TOUCH_KEYS
                    if owner_is_button
                    else inputs.KEYBOARD_KEYS
                    if objs[c].type_code == inputs.KEYBOARD_TC
                    else inputs.TOUCH_KEYS
                )
                tmpl = self._template_values(c)
                for k in names:
                    v = str(vals.get(k) or "")
                    if v != str(tmpl.get(k) or ""):
                        self.set_member_property(c, k, v)
                self._rederive_member(c, objs[c], "CMD", " ".join(map(str, vals.values())))
        return bool(changed)

    def apply_symbol_set(self, index: int, kind: int, exprs, states, label: str = "symbol set") -> int:
        """Apply the Symbol Set sheet inside a genie."""
        if not self._template_record(index):
            return super().apply_symbol_set(index, kind, exprs, states, label)
        from .format import symbolset as S

        exprs = [str(e or "") for e in (exprs or [""])]
        if int(kind) == S.MULTI_STATE:
            exprs = exprs[: S.MAX_CONDITIONS] or [""]
            while len(exprs) > 1 and not exprs[-1]:
                exprs.pop()
        else:
            exprs = exprs[:1]
        had_mode = "TXTMODE" in self.object_properties(index)
        with self.batch(label):
            index = super().apply_symbol_set(index, kind, exprs, states, label)
            keys = S.expression_keys(int(kind), len(exprs) or 1)
            vals = list(exprs) + [""] * (len(keys) - len(exprs))
            if had_mode or int(kind) != S.ON_OFF:
                self.set_member_property(index, "TXTMODE", str(int(kind)))
            for key, val in zip(keys, vals):
                self.set_member_property(index, key, val)
            for key in S.EXPRESSION_KEYS:
                if key not in keys and key in self.object_properties(index):
                    self.set_member_property(index, key, "")
            # (the page rule's writer may have put the expressions into the template
            # bag already, leaving the edits above nothing to do: the values,
            # declarations -- a new %param% -- and text are derived again regardless)
            self._rederive_member(index, self.parsed.page.objects[index], "TXT_A", " ".join(exprs))
        return index

    def set_metadata(self, index: int, items) -> bool:
        """A genie-element member's metadata goes to its TEMPLATE bag like
        every property (set_member_property), one undo step."""
        if not self._template_record(index):
            return super().set_metadata(index, items)
        items = [(str(n), str(v)) for n, v in items]
        msg = metadata_problem(items)
        if msg:
            raise ValueError(msg[1])
        if items == self.metadata(index):
            return False
        old = [k for k in self.object_properties(index) if k.upper().startswith("METADATA_")]
        with self.batch("metadata"):
            for k in old:
                self.set_member_property(index, k, "")
            for n, (name, value) in enumerate(items):
                self.set_member_property(index, "METADATA_%d" % n, "%s=%s" % (name, value))
        return True

    def set_alarm_indicator(self, index: int, values: dict) -> bool:
        """Set a genie group's alarm indicator in its template bag."""
        if not self._template_record(index):
            return super().set_alarm_indicator(index, values)
        from .format import alarmind as AI

        if self.parsed.page.objects[index].type_code != 0x08:
            return False
        cur = self.alarm_indicator(index)
        want = dict(cur)
        want.update(values)
        if want == cur:
            return False
        edits = AI.apply_edits(want)
        guard_text(*[v for _k, v in edits if v])
        with self.batch("alarm indicator"):
            for k, v in edits:
                self.set_member_property(index, k, v or "")
        return True

    def ensure_property(self, index: int, key: str, value: str) -> bool:
        if self._template_record(index):
            return self.set_member_property(index, key, value)
        return super().ensure_property(index, key, value)

    def remove_property(self, index: int, key: str) -> bool:
        if self._template_record(index):
            return self.set_member_property(index, key, "")
        return super().remove_property(index, key)

    def set_text(self, index: int, text: str) -> bool:
        """A genie-element text / button caption is its TEMPLATE (TXT_OFF /
        TEXT, may carry %params%; a wrapped '@(...)' caption stays wrapped)."""
        text = crlf_text(text)
        if self._template_record(index):
            from .format.genie_place import decode

            po = self.parsed.page.objects[index]
            key = "TXT_OFF" if po.type_code == 0x05 else "TEXT"
            old = decode(po).entries[0][2].get(key) or ""
            if old.startswith("@(") and old.endswith(")") and not text.startswith("@("):
                text = "@(%s)" % text
            return self.set_member_property(index, key, text)
        return super().set_text(index, text)

    # -- save --------------------------------------------------------------------
    def thumbnail_geometry(self):
        """((x, y, w, h) scene rect, (tw, th)) of the element's TH thumbnail
        (elements are not page-scaled: longest side 200, never enlarged)."""
        from .format.element_session import forest_roots, union_rects, thumbnail_geometry

        if self.session is not None:
            return thumbnail_geometry(self.session, self.parsed)
        objs = self.parsed.page.objects
        if self._page_size:
            x, y, (w, h) = 0, 0, self._page_size
        else:
            _u1, u2 = union_rects(objs, forest_roots(objs)) if objs else (None, None)
            if u2 is None:
                return None
            x, y, w, h = u2[0], u2[1], u2[2] - u2[0], u2[3] - u2[1]
        if w <= 0 or h <= 0:
            return None
        s = min(200.0 / w, 200.0 / h, 1.0)
        if s >= 1.0:
            return (x, y, w, h), (w, h)
        return (x, y, w, h), ((200, max(1, int(h * s))) if w >= h else (max(1, int(w * s)), 200))

    @one_read
    def serialize_element(
        self,
        thumbnail: Optional[bytes] = None,
        refresh: bool = False,
        memo: Optional[dict] = None,
        every_nested: bool = False,
        report: Optional[dict] = None,
    ) -> bytes:
        """The element content a save would write."""
        from .format import element as E
        from .format.element_session import session_to_content

        self._use_tag_formats()
        res = self._resolver()
        if self.session is not None:
            args = self._content_args(res, every_nested)
            return session_to_content(
                self.session, args.pop("parsed"), thumbnail, refresh=refresh, memo=memo, report=report, **args
            )
        # a NEW element's first save too: a placeholder pasted into it whose library opens is
        # the genie again, and its bare entry the genie's current one
        src, carried = self.parsed.page.objects, self._link_entries_now()[1]
        try:
            # (in the page form: new_element_content converts every record afterwards)
            st = self.placeholder_genie_state(page_form=True)
        except Exception:  # noqa: BLE001 -- saved with its markers
            from .infra import logsetup

            logsetup.note_exception(
                "editing.ElementDocument.serialize_element/new placeholders", page=self.path
            )
            st = None
        if st is not None:
            from .format import links as _links

            src = st[0]
            t = _links.parse_link_table(st[1]) if st[2] else None
            carried = list(t[1]) if t else []
        objs = [parse_object(0, 0, len(b), 0, b) for b in (writer.object_payload(o) for o in src)]
        if not objs:
            raise ValueError(
                "A library element needs at least one object. Draw something "
                "before you save %s." % self.display_name
            )
        at = self.parsed.attributes
        ext = os.path.splitext(self.path)[1].lower().lstrip(".")
        if self._page_size:
            origin, page_size = (0, 0), (self.parsed.page.page_width, self.parsed.page.page_height)
        else:
            from .format.element_session import forest_roots, union_rects

            u1, _u2 = union_rects(objs, forest_roots(objs))
            origin = ((u1[0] + u1[2]) // 2, (u1[1] + u1[3]) // 2) if u1 else (0, 0)
            if getattr(self, "_hotspot", None) is not None:
                origin = tuple(self._hotspot)  # set before the first save
            page_size = None
        trailer = E.default_trailer(list(at.list1.pairs), list(at.list2.pairs), (at.bg_color0, at.bg_color1))
        # (the entries a paste carried: a stale copy keeps its old stamp where it is not
        # composed, so the new element still reads as out of date for it)
        return self._with_attachments(
            E.new_element_content(
                objs,
                origin=origin,
                page_size=page_size,
                trailer=trailer,
                thumbnail=thumbnail or b"",
                resolver=res,
                ext=ext,
                carried=carried,
                lib_path=self.path,
            )
        )

    def _content_args(self, res, every_nested: bool = False) -> dict:
        """The arguments serialize_element passes to session_to_content."""
        # a placed library symbol not re-derived yet keeps its old stamp (save
        # re-derives it first): the element stays out of date for it
        try:
            keep = frozenset(self.stale_symbol_elements())
        except Exception:  # noqa: BLE001 -- the stamps are made current as before
            from .infra import logsetup

            logsetup.note_exception("editing.ElementDocument.serialize_element/symbols", page=self.path)
            keep = frozenset()
        nested = None if every_nested else self._changed_nested(res)
        if nested is None:
            # every copy (the command, or no import table to tell the changed ones):
            # named one by one, so a copy that no longer lines up with its genie is
            # composed again structurally and one that cannot be keeps its old stamp
            # (a template's copy only when its stamp moved: a current one is kept, the
            # composition does not give every stored copy back record for record)
            from .format import genie_place as GP

            nested = GP.nested_keys(self.parsed.page.objects) | (self._changed_nested(res) or frozenset())
        # a PLACEHOLDER genie the element holds (CtDraw's save of it while a nested
        # genie's library did not open) is the genie again once its library opens, as
        # CtDraw's element load + save places it (with or without the bare entry). Written
        # so; the document's records stay
        parsed = self.parsed
        try:
            st = self.placeholder_genie_state()
        except Exception:  # noqa: BLE001 -- saved with its markers
            from .infra import logsetup

            logsetup.note_exception("editing.ElementDocument.serialize_element/placeholders", page=self.path)
            st = None
        if st is not None:
            import copy as _copy

            parsed = _copy.copy(self.parsed)
            pg = parsed.page = _copy.copy(self.parsed.page)
            pg.objects = list(st[0])
            pg.object_count = len(pg.objects)
            pg.link_table_raw, pg.link_count = st[1], st[2]
        return {
            "parsed": parsed,
            "edited_handles": self._template_edited,
            "resolver": res,
            "instantiated": getattr(self, "_instantiated", ()),
            "origin": getattr(self, "_hotspot", None),
            "nested_only": nested,
            "keep_stamps": keep,
        }

    def _content_key(self, refresh: bool, every_nested: bool) -> tuple:
        """The key under which session_to_content's memo holds the records a save with
        ``refresh`` / ``every_nested`` normalises (element_session.content_key)."""
        from .format import element_session as ES

        self._use_tag_formats()
        args = self._content_args(self._resolver(), every_nested)
        parsed = args.pop("parsed")
        return ES.content_key(
            [writer.object_payload(o) for o in parsed.page.objects], parsed, refresh=refresh, **args
        )

    def content_memo_request(
        self, refresh: bool, every_nested: bool = False, thumbnail: bool = False
    ) -> dict:
        """What a helper process needs to normalise this element's save."""
        req = self.nested_update_request()
        req["save"] = {
            "refresh": bool(refresh),
            "every_nested": bool(every_nested),
            "instantiated": sorted(getattr(self, "_instantiated", ()) or ()),
            "thumbnail": bool(thumbnail),
        }
        return req

    def content_memo(self, refresh: bool, every_nested: bool = False, thumbnail_for=None) -> dict:
        """In a helper process: this element's save normalisation, as JSON."""
        from .format import element_session as ES

        memo: dict = {}
        content = self.serialize_element(refresh=refresh, memo=memo, every_nested=every_nested)
        out = ES.memo_to_json(memo)
        if thumbnail_for is not None:
            th = self._thumbnail_as_stored(content, thumbnail_for)
            if th:
                out["thumbnail_as_stored"] = bytes(th).hex()
        return out

    def memo_from_helper(self, answer: dict, refresh: bool, every_nested: bool = False) -> Optional[dict]:
        """The memo of a helper's :meth:`content_memo` answer for this save, under this
        document's own key: None when the helper normalised other records or with other
        choices (the save then normalises here)."""
        from .format import element_session as ES

        return ES.memo_from_json(answer, self._content_key(refresh, every_nested))

    def _changed_nested(self, res):
        """Keys of nested genies that changed since this element was saved."""
        pb = self.parsed.page
        if res is None or pb is None or not pb.link_count:
            return None
        try:
            from .io import updatepages

            ch = updatepages.changed_genies(pb.link_table_raw, res)
            tpl = updatepages.changed_templates(pb.link_table_raw, res)
            if tpl:
                from .format import template_place as TP

                tree = self._tree()
                tpl &= TP.nested_template_keys(tree)
                ref = getattr(self, "element_ref", None)
                if tpl and ref and self.path.lower().endswith(".ctt"):
                    # (never a copy of this template itself, or of one holding its copy:
                    # its own copy is out of date after every save of it, and each save
                    # would nest the stored version once more; such a save is refused,
                    # _refuse_self_link)
                    for a, b, r in TP.nested_template_spans(tree):
                        if TP.template_nesting_chain(tree[a:b], self.path, ref[1], res) is not None:
                            tpl.discard((r.library.lower(), r.element.lower()))
        except Exception:  # noqa: BLE001
            from .infra import logsetup

            logsetup.note_exception("editing.ElementDocument._changed_nested", page=self.path)
            return None
        keys = set(tpl)
        for key, nested in ch.items():
            if nested is None:
                keys.add(key)
            else:
                keys |= set(nested)
        return frozenset(keys)

    # -- the genies nested in the element: CtDraw's element load composes them ----------
    def nested_genie_changes(self) -> frozenset:
        """(library, element) of nested genies that changed since this element was saved."""
        if self.session is None:
            return frozenset()
        keys = set(self._changed_nested(self._resolver()) or ())
        try:
            keys |= {(lib.lower(), el.lower()) for _i, lib, el, _x in self.placeholder_genies()}
            # (not the genie of a placeholder that would make this genie hold itself: it is
            # never placed, so its bare entry must not make the tab's automatic update
            # change the genie; its save is refused, _refuse_self_link)
            keys -= self._self_placeholder_keys()
        except Exception:  # noqa: BLE001 -- the stamps' answer
            from .infra import logsetup

            logsetup.note_exception("editing.ElementDocument.nested_genie_changes", page=self.path)
        return frozenset(keys)

    def _self_placeholder_keys(self) -> set:
        """(library, element), lower case, of the PLACEHOLDER genies this genie holds that
        are this genie or hold a copy of it at any depth (genie_place.nesting_chain):
        placed, it would hold itself (CtDraw does not open such a genie)."""
        from .format import genie_place as GP

        ref = self.element_ref
        if not ref or not self.path.lower().endswith(".ctm"):
            return set()
        out = set()
        res = None
        for po in self.parsed.page.objects:
            got = GP.placeholder_genie(po)
            if got is None:
                continue
            r = got[0]
            key = (r.library.lower(), r.element.lower())
            if key in out:
                continue
            path = self.library_path(r.library, r.element, "ctm")
            if path is None:
                continue
            res = res or self._resolver()
            if GP.nesting_chain(path, r.element, self.path, ref[1], res) is not None:
                out.add(key)
        return out

    def nested_genie_sources(self) -> frozenset:
        """The library files (path, mtime, size, content tag) the genies nested in this
        element are composed from (io/updatepages.library_files over its import table): two
        equal answers = nothing they are made of changed on disk in between."""
        pb = self.parsed.page
        res = self._resolver()
        if res is None or pb is None or not pb.link_count:
            return frozenset()
        from .io import updatepages

        return updatepages.library_files(pb.link_table_raw, res)

    @one_read
    def nested_update_ops(self, every: bool = False, report: Optional[dict] = None):
        """The records stale nested genies change."""
        from .workers import genie_worker as GW
        from .format import element as E
        from .format.element_session import open_element_session

        if report is not None and every:
            report["changed_copies"] = []
        if self.session is None:
            return [], None
        nested = frozenset() if every else self.nested_genie_changes()
        if not (every or nested):
            return [], None
        # (a placeholder whose genie opens again: placed, also by Compose Nested Genies Again)
        held = (
            [(lib_.lower(), el_.lower()) for _i, lib_, el_, _x in self.placeholder_genies()] if every else []
        )
        if every:
            from .format import genie_place as GP
            from .format import template_place as TP

            objs = self.parsed.page.objects
            if not GP.nested_keys(objs) and not TP.nested_template_spans(objs) and not held:
                if report is not None:
                    report["stale_nested"] = frozenset()
                return [], None  # (it nests no genie nor template: nothing to compose)
        content = self.serialize_element(refresh=True, every_nested=every, report=report)
        # the element as it would be stored, in memory only: its session records
        lib = E.LibraryFile(self.session.pe.library.to_bytes())
        lib.write_element(self.session.pe.name, content)
        _s, parsed = open_element_session(self.path, self.session.pe.name, library=lib)
        if every:
            from .format import copy_compare

            changed = copy_compare.changed_copies(self.parsed.page.objects, parsed.page.objects)
            seen = {(k[0].lower(), k[1].lower()) for k in changed if k is not None}
            changed = list(changed) + [k for k in held if k not in seen]
            if report is not None:
                report["changed_copies"] = changed
            if not changed:
                return [], None  # (only a save's normalisation: the copies match)
        before = [writer.object_payload(o) for o in self.parsed.page.objects]
        after = [writer.object_payload(o) for o in parsed.page.objects]
        ops = GW.record_ops(before, after)
        if not ops:
            return [], None  # (only the stamps: the next save writes them)
        pb, nb = self.parsed.page, parsed.page
        link = None
        if (nb.link_table_raw or b"", nb.link_count) != (pb.link_table_raw or b"", pb.link_count):
            link = (nb.link_table_raw or b"", nb.link_count, pb.link_table_raw or b"", pb.link_count)
        if report is not None:
            # (the handles its new records took, the copies it held, the genies it composed:
            # an answer applied to the tab as the user changed it meanwhile,
            # genie_worker.answer_facts)
            composed = set(nested) | {
                (k[0].lower(), k[1].lower()) for k in (report.get("changed_copies") or ()) if k is not None
            }
            report["answer_facts"] = GW.answer_facts(
                GW.handle_facts(pb.objects),
                nb.objects,
                GW.composed_references(composed, GW.genie_copy_spans(pb.objects), ops),
            )
        return ops, link

    def update_nested_genies(
        self, label: str = "update genies from library", every: bool = False, report: Optional[dict] = None
    ) -> int:
        """Recompose the nested genie copies that changed since this element was saved."""
        ops, link = self.nested_update_ops(every=every, report=report)
        if not ops:
            return 0
        return self.apply_record_ops(ops, link, label=label) or 0

    def apply_genie_changes(self, changes: dict, placeholders=None, force: bool = False) -> int:
        """In an element (ui/jobs/genie_jobs' job run in this process): the stale nested genies'
        copies composed again (:meth:`update_nested_genies`)."""
        return self.update_nested_genies()

    def nested_update_request(self) -> dict:
        """What a worker process needs to compute :meth:`nested_update_ops` for this tab
        (genie_worker.compute_element; the session page's bytes go beside it): the element,
        the stored block it was opened from (sha1: the worker refuses another one), the
        members whose template was edited, a moved hot spot."""
        import hashlib

        hs = getattr(self, "_hotspot", None)
        return {
            "library": self.path,
            "element": self.session.pe.name,
            "block_sha1": hashlib.sha1(bytes(self.session.pe.block.data)).hexdigest(),
            "edited": sorted(int(h) for h in (self._template_edited or ())),
            "hotspot": list(hs) if hs is not None else None,
        }

    @classmethod
    def for_nested_update(cls, req: dict, data: bytes) -> "ElementDocument":
        """The element document for a worker request."""
        import hashlib
        from .format.element_session import open_element_session
        from .format.page import parse_ctg_bytes

        lib_path, name = req["library"], req["element"]
        sess, _parsed = open_element_session(lib_path, name)
        if hashlib.sha1(bytes(sess.pe.block.data)).hexdigest() != req.get("block_sha1"):
            raise ElementChangedMeanwhile("%s changed in its library since the tab opened it" % name)
        doc = cls(path=lib_path)
        doc.session = sess
        doc.parsed = parse_ctg_bytes(bytes(data))
        doc.element_ref = (lib_path, sess.pe.name, sess.pe.kind)
        doc._template_edited = set(int(h) for h in req.get("edited") or ())
        hs = req.get("hotspot")
        doc._hotspot = tuple(int(v) for v in hs) if hs else None
        return doc

    @property
    def previous_element(self):
        """The element as stored before the last save, or None."""
        el = self.__dict__.get("_previous_element", _NO_ELEMENT)
        if el is _NO_ELEMENT:
            data = self.__dict__.pop("_previous_bytes", None)
            el = self._snapshot_element(data) if data is not None else None
            self.__dict__["_previous_element"] = el
        return el

    @previous_element.setter
    def previous_element(self, value) -> None:
        self.__dict__["_previous_element"] = value
        self.__dict__.pop("_previous_bytes", None)

    def _snapshot_element(self, data: Optional[bytes] = None):
        """The element as it is stored in the library bytes ``data`` (default: the
        session's, the version before this save overwrites it), as a genie_place.Element:
        pages placing it are updated only where composing from this version reproduces
        their instance."""
        from .format import genie_place as GP

        if data is None:
            if self.session is None:
                return None
            data = self.session.pe.library.to_bytes()
        try:
            try:
                with open(self.path, "rb") as fh:
                    same = fh.read() == data
            except OSError:
                same = False
            if same:
                # the library on disk IS the stored version: load it where it is
                # (the same bytes give the same element; the element memo usually
                # has it already -- the pages placing it loaded it)
                el = GP.load_element(self.path, self.element_name, resolver=self._resolver(), compose=True)
                el.library, el.element = self.library_name, self.element_name
                el.lib_path = self.path
                return el
            tmp = tempfile.mkdtemp(prefix="ctgedit_prev_")
            path = os.path.join(tmp, os.path.basename(self.path))
            with open(path, "wb") as fh:
                fh.write(data)
            try:
                return self._element_from_copy(path)
            finally:
                import shutil

                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            _note_exc("editing.ElementDocument._snapshot_element", path=self.path)
            return None

    def _element_from_copy(self, copy_path: str):
        """This element as the library file ``copy_path`` (a copy of its library as it
        was) stores it, composed with this element's own library folders, named as it is."""
        from .format import genie_place as GP

        el = GP.load_element(copy_path, self.element_name, resolver=self._resolver(), compose=True)
        el.library, el.element = self.library_name, self.element_name
        el.lib_path = self.path
        return el

    def previous_bytes(self) -> Optional[bytes]:
        """The library bytes the last save of this genie overwrote (its version before the
        save: the open pages that place it take only the edit, genie_worker.apply_update);
        None when there are none (not saved here, or the save wrote nothing)."""
        return self.__dict__.get("_previous_bytes")

    @classmethod
    def previous_version(cls, lib_path: str, element: str, copy_path: str):
        """Genie ``element`` as ``copy_path`` (a copy of its library ``lib_path`` before a
        save, :meth:`previous_bytes`) stores it (genie_place.Element), composed and named as
        the library at ``lib_path`` would (in the genie worker: genie_worker.apply_update).
        None when it cannot be read."""
        if not lib_path:
            return None
        doc = cls(path=lib_path)
        doc.element_ref = (lib_path, element, "genie")
        try:
            return doc._element_from_copy(copy_path)
        except Exception:  # noqa: BLE001 -- the instances are then left as they are
            from .infra import logsetup

            logsetup.note_exception("editing.ElementDocument.previous_version", page=lib_path)
            return None

    def save(
        self,
        path: str | None = None,
        name: Optional[str] = None,
        thumbnail: Optional[bytes] = None,
        page_thumbnail=None,
        refresh: bool = False,
        expect_stored: bool = False,
        thumbnail_for=None,
        every_nested: bool = False,
        report: Optional[dict] = None,
        prepare=None,
    ) -> str:
        """Write the element back into its library, or into another one (Save As)."""
        from .format import element as E

        target = path or self.path
        name = name or self.element_name
        self._element_save_checks(target, name)
        moved = path_key(target) != path_key(self.path) or name.lower() != self.element_name.lower()
        self._refuse_self_link(target, name)
        redraw = False
        if thumbnail is not None and thumbnail_for is not None and not moved:
            try:
                redraw = self._carries_older(self._link_entries_now()[1])
            except Exception:  # noqa: BLE001 -- the caller's picture is stored
                from .infra import logsetup

                logsetup.note_exception("editing.ElementDocument.save/redraw", page=target)
        # the attached super genies' pages first: the bind a page is created
        # from is written with the target's identity (serialize_element)
        pages = self._create_super_pages(target, page_thumbnail) if self.kind == "genie" else []
        self._instantiated = {r["element"].lower() for r in pages if r.get("status") == "created"}
        refresh = self._element_save_refresh(target, refresh, every_nested)
        try:
            memo: dict = {}  # (the records are normalised once)
            if prepare is not None and self.session is not None:
                try:
                    memo = prepare(self, refresh, every_nested, redraw) or {}
                except Exception:  # noqa: BLE001 -- normalised here instead
                    from .infra import logsetup

                    logsetup.note_exception("editing.ElementDocument.save/prepare", page=target)
                    memo = {}
            content = self.serialize_element(
                refresh=refresh, memo=memo, every_nested=every_nested, report=report
            )
            unchanged = (
                not moved
                and self.session is not None
                and E.stored_content_equal(target, name, content, ignore_identity=True)
            )
            # (drawn by the helper that normalised it, when it did: prepare)
            drawn = memo.get("thumbnail_as_stored") if redraw else None
            if not unchanged and thumbnail is None and thumbnail_for is not None and not moved:
                thumbnail = self._thumbnail_as_stored(content, thumbnail_for)
            elif not unchanged and redraw:
                # (the save brought a copy up to date: the caller's picture is the old one)
                thumbnail = drawn or self._thumbnail_as_stored(content, thumbnail_for) or thumbnail
            if not unchanged and thumbnail is not None:
                content = self.serialize_element(
                    thumbnail, refresh=refresh, memo=memo, every_nested=every_nested
                )
        finally:
            self._instantiated = ()
        self.previous_element = None
        if not unchanged:
            self._element_store(target, name, content, moved, expect_stored)
        else:
            self.save_warning = ""
            self.save_files = []
            if (
                not moved
                and self.session is not None
                and not self.dirty
                and not self._undo
                and not self._redo
                and self._disk_stamp is not None
                and self._disk_stamp == self.disk_stamp(target)
            ):
                # nothing written, and the records are the stored ones already (read from
                # this very file, never edited): the reopen below would read them again
                self.super_pages = pages
                return target
        # what CtDraw's save leaves of the turn memory, as a page save does
        # (GeometryEdits._turn_memo_after_save; history.py _after_page_write)
        if not getattr(type(self), "REVERSIBLE_TURNS", False):
            self._turn_memo_after_save()
        self._take_stored(target, name)
        self.super_pages = pages
        return target

    @staticmethod
    def _element_save_checks(target: str, name: str) -> None:
        """Refuse a save into a composition cache, or under a name a library cannot hold."""
        from .format.composites import in_composition_cache

        if in_composition_cache(target):
            # only Plant SCADA's composer writes a composition cache (help 1172220)
            raise ValueError(
                ("%s is in a composition cache, which only Plant SCADA writes. ctgedit does not save there.")
                % target
            )
        textcodec.encode_field(name, "a library element name")  # never a '?'
        if len(name.encode(CODEC, "replace")) > 31:
            raise ValueError("Library element names can be at most 31 characters.")

    def _element_save_refresh(self, target: str, refresh: bool, every_nested: bool) -> bool:
        """Bring the placed symbols up to date; whether the save composes the nested
        genies' copies again (``refresh``, also when one of them changed)."""
        # a library symbol placed in it changed since it was saved: re-derived from its
        # current element first, as Update Pages does on a page (the import table then
        # quotes its new stamp). A new element's first save too: a stale copy pasted into it
        try:
            sym = self.stale_symbol_elements() if self.symbol_placements() else set()
            if sym:
                self.update_symbol_placements(sym)
                refresh = True
        except Exception:  # noqa: BLE001 -- saved as edited
            from .infra import logsetup

            logsetup.note_exception("editing.ElementDocument._element_save_refresh/symbols", page=target)
        if every_nested:
            # (Update Pages' 'compose their nested genies again': every copy, whatever the
            # import table's stamps say, as Edit > Library > Compose Nested Genies Again)
            refresh = True
        if not refresh and self.session is not None:
            # a genie nested in it changed since it was saved: CtDraw's load + save
            # composes its copies again, also when nothing else was edited
            try:
                refresh = bool(self.nested_genie_changes())
            except Exception:  # noqa: BLE001 -- saved as edited
                from .infra import logsetup

                logsetup.note_exception("editing.ElementDocument._element_save_refresh/nested", page=target)
        return refresh

    def _element_store(
        self, target: str, name: str, content: bytes, moved: bool, expect_stored: bool
    ) -> None:
        """Store ``content`` as element ``name`` of the library ``target``, with a new
        identity stamp; the version it replaces is kept (Page History)."""
        from .format import element as E

        if not moved and self.session is not None and self.kind == "genie":
            # the stored version as bytes: its Element is made when a page asks for it
            # (previous_element; most saves have no open page placing the genie)
            self.__dict__.pop("_previous_element", None)
            self._previous_bytes = self.session.pe.library.to_bytes()
        stamp = E.ctdraw_timestamp()
        content = E.refresh_identity(content, stamp)
        lib = self.session.pe.library if self.session is not None else None
        # the element as the library stores it now: an earlier version (Page History)
        # (the first save after File > Page History > Restore keeps what it replaces)
        self._keep_element_version(target, name, force=getattr(self, "restored_version", None) is not None)
        self.restored_version = None
        E.store_element(
            target,
            name,
            content,
            stamp=stamp,
            newer=True,
            version=lib.version if lib else 9,
            word2=lib.word2 if lib else 0x0C,
            expect=bytes(self.session.pe.block.data)
            if expect_stored and not moved and self.session is not None
            else None,
        )
        libraries_changed()
        self.save_warning = ctdraw_cache_warning()
        self.save_files = [target]
        from .infra import logsetup

        logsetup.files_written(
            "saved %s %s.%s" % (self.kind, os.path.splitext(os.path.basename(target))[0], name), [target]
        )

    def _refuse_self_link(self, target: str, name: str) -> None:
        """Refuse a save that would make this genie contain itself."""
        low = target.lower()
        if not low.endswith((".ctm", ".ctt")) or self.parsed is None or self.parsed.page is None:
            return
        from .format import genie_place as GP
        from .format import template_place as TP

        res = self._resolver()
        if res is None:
            return
        if low.endswith(".ctt"):
            chain = TP.template_nesting_chain(self.parsed.page.objects, target, name, res)
        else:
            chain = GP.records_nesting_chain(self.parsed.page.objects, target, name, res)
        if chain is not None:
            label = "%s.%s" % (os.path.splitext(os.path.basename(target))[0], name)
            raise ValueError(self_link_refusal([label] + list(chain)))

    def _thumbnail_as_stored(self, content: bytes, render) -> Optional[bytes]:
        """``render(doc)`` of this element as the library would store ``content``: the
        stored library with it, in memory, opened as an element document (not written; a
        new element in an empty library). None when the render fails (noted: the stored
        thumbnail stays)."""
        try:
            from .format import element as E
            from .format.element_session import open_element_session

            if self.session is not None:
                lib = E.LibraryFile(self.session.pe.library.to_bytes())
                name = self.session.pe.name
            else:
                lib, name = E.LibraryFile.new(), self.element_name
            lib.write_element(name, content)
            sess, parsed = open_element_session(self.path, name, library=lib)
            doc = type(self)(path=self.path)
            doc.session = sess
            doc.parsed = parsed
            doc.element_ref = (self.path, sess.pe.name, sess.pe.kind)
            doc._template_edited = set()
            doc._derive(fresh=True)
            return render(doc) or None
        except Exception:  # noqa: BLE001 -- the stored thumbnail then
            from .infra import logsetup

            logsetup.note_exception("editing.ElementDocument.save/thumbnail", page=self.path)
            return None

    def reload_stored(self, opened=None) -> None:
        """Take the element as its library stores it now as this tab's baseline, after
        another process saved it from this tab's clean state (the chain after a genie save,
        genie_worker.update_closed_pages): what the tab's own save does after it wrote it.
        ``opened`` = (session, parsed) of :meth:`stored_session`, read already."""
        if self.session is None:
            return
        self._take_stored(self.path, self.session.pe.name, opened=opened)

    def stored_session(self):
        """(session, parsed) of the element as its library stores it now
        (element_session.open_element_session); None for a new element."""
        if self.session is None:
            return None
        from .format.element_session import open_element_session

        return open_element_session(self.path, self.session.pe.name)

    def stored_block_sha1(self) -> Optional[str]:
        """The SHA-1 (hex) of this element's block as its library stores it now, None when
        it is not stored (or the library cannot be read)."""
        if self.session is None:
            return None
        from .format.element import LibraryFile

        try:
            lib = LibraryFile.open(self.path)
        except OSError:  # swallow-ok: not readable now: no version to compare
            return None
        s = lib.find(self.session.pe.name)
        return hashlib.sha1(bytes(lib.block_data(s))).hexdigest() if s is not None else None

    def baseline_block_sha1(self) -> Optional[str]:
        """The SHA-1 (hex) of the block this tab holds as stored (what 'changed on disk'
        compares with), None for a new element."""
        if self.session is None:
            return None
        return hashlib.sha1(bytes(self.session.pe.block.data)).hexdigest()

    def adopt_disk_version(self, opened=None) -> None:
        """Take the library's current copy of the element as this tab's baseline."""
        if self.session is None:
            return
        sess, _parsed = opened or self.stored_session()
        self.session = sess
        self.element_ref = (self.path, sess.pe.name, sess.pe.kind)
        self._disk_stamp = self.disk_stamp(self.path)
        self.read_only = self.file_read_only(self.path)

    def _take_stored(self, target: str, name: str, opened=None) -> None:
        """The stored (engine-normal) element ``target``/``name`` is the new baseline
        (``opened``: its (session, parsed), read already)."""
        from .format.element_session import open_element_session

        # patch the session records IN PLACE (the undo stack holds these objects) and
        # take the re-opened session's change baseline
        sess, parsed = opened or open_element_session(target, name)
        objs = self.parsed.page.objects
        pb0 = self.parsed.page
        if self._undo and [writer.object_payload(p) for p in objs] != [
            writer.object_payload(p) for p in parsed.page.objects
        ]:
            # the stored records differ from the ones the undo steps lead to (the save
            # composed nested copies, normalised): undoing past this point takes the table
            # those records came with (history._leave_save_point). The first one at a depth
            # is kept (a second save with no step between composes from the same records)
            pts = self.__dict__.setdefault("_presave_tables", {})
            pts.setdefault(self.history_depth, (pb0.link_table_raw, pb0.link_count))
        if len(parsed.page.objects) == len(objs):
            for po, npo in zip(objs, parsed.page.objects):
                po.__dict__.update(npo.__dict__)
        else:
            objs[:] = parsed.page.objects
        self.parsed.page.object_count = len(objs)
        self.parsed.page.link_table_raw = parsed.page.link_table_raw
        self.parsed.page.link_count = parsed.page.link_count
        self.session = sess
        self.path = target
        self.element_ref = (target, sess.pe.name, sess.pe.kind)
        self._disk_stamp = self.disk_stamp(target)
        self.read_only = self.file_read_only(target)
        self._template_edited = set()
        self._page_size = None
        self._hotspot = None  # the saved origin is the session's now
        self.dirty = False
        self._rerender_page()

    @staticmethod
    def _keep_element_version(target: str, name: str, force: bool = False) -> None:
        """Keep element ``name`` as library ``target`` stores it (before a save replaces
        it) as an earlier version: a library holding that element alone (infra/history.py).
        Nothing when versions are off or the element is not stored yet; never raises.
        ``force``: also within Settings 'New earlier version at most every'."""
        from .infra import history as H

        if not H.enabled() or not os.path.exists(target):
            return
        from .format import element as E

        try:
            pe = E.open_element(target, name)
        except KeyError:  # swallow-ok: a new element (or a new name): nothing stored yet
            return
        except Exception:  # noqa: BLE001 -- the save goes on without the copy
            from .infra import logsetup

            logsetup.note_exception("editing.save/element_version", page=target)
            return
        try:
            body = bytes(pe.block.data)
            content = body[: len(body) - len(pe.slack)] if pe.slack else body
            one = E.LibraryFile.new(pe.library.version, pe.library.word2, pe.library.ts_modified)
            one.write_element(pe.name, content)
            data = one.to_bytes()
        except Exception:  # noqa: BLE001 -- an old library form, no room: no copy
            from .infra import logsetup

            logsetup.note_exception("editing.save/element_version", page=target)
            return
        from .doc.history import keep_earlier_version

        keep_earlier_version(target, data, element=pe.name, force=force)

    def _create_super_pages(self, target: str, page_thumbnail=None) -> List[dict]:
        """Create the pages of attached super genies the project doesn't have yet."""
        from .format import supergenie as SG

        pages: List[dict] = []
        with contextlib.suppress(Exception):
            targets = SG.attachment_targets(self._link_entries_now()[1])
            pages = create_super_genie_pages(
                os.path.dirname(os.path.abspath(target)),
                targets,
                self._resolver(),
                thumbnail=page_thumbnail,
                placing=False,
            )
        return pages


# the process caches of this module (infra/caches.py: when each is emptied)
from .infra import caches as _caches  # noqa: E402

_caches.register(
    "editing.merge_decoded",
    EditableDocument,
    clear=lambda: setattr(EditableDocument, "_MERGE_DECODED", {}),
    on=("tests",),
)


#: the documents with undo history (one undo budget for the process)
_UNDO_DOCS: "weakref.WeakSet" = weakref.WeakSet()
#: the undo history all open documents may keep together (Settings > Editing > Undo memory:
#: ui/prefs/settings.apply_undo_budget); one page alone keeps at most two thirds of it
#: (_undo_page_cap)
UNDO_BYTES_TOTAL = 384 * 1024 * 1024


def _undo_page_cap() -> int:
    """The most undo history one page keeps: two thirds of UNDO_BYTES_TOTAL, so 256 MB at the
    384 MB default."""
    return UNDO_BYTES_TOTAL * 2 // 3


def _undo_total(doc) -> int:
    return sum(command_bytes(c) for c in getattr(doc, "_undo", ()) or ())


def _trim_undo_everywhere(current) -> None:
    """Past UNDO_BYTES_TOTAL together, drop the oldest undo steps of the document with the
    largest history (each keeps its newest UNDO_KEEP_MIN steps and any step after an open
    history mark, as _trim_undo does)."""
    docs = [d for d in list(_UNDO_DOCS) if getattr(d, "_undo", None)]
    if not docs:
        return
    totals = {id(d): _undo_total(d) for d in docs}
    total = sum(totals.values())
    while total > UNDO_BYTES_TOTAL:
        cand = [
            d
            for d in docs
            if len(d._undo) > UNDO_KEEP_MIN
            and not getattr(d, "_batching", 0)
            and getattr(d, "_mark_floor", None) is None
        ]
        if not cand:
            return
        d = max(cand, key=lambda x: totals[id(x)])
        drop = command_bytes(d._undo[0])
        d._saved_point_trimmed(d._undo[:1])
        del d._undo[0]
        d.undo_trimmed = getattr(d, "undo_trimmed", 0) + 1
        totals[id(d)] -= drop
        total -= drop
