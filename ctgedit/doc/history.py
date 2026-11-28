"""Undo, redo and save."""

from __future__ import annotations

from ..infra.paths import path_key

from ..format.projects import is_project_dir

from ..editing_base import (  # noqa: F401
    CODEC,
    Optional,
    PageChangedOnDisk,
    PageRecordError,
    ProjectBusyError,
    ProjectDatabaseInUse,
    ctf_matches,
    file_identity,
    os,
    write_atomic,
    writer,
)
from ..editing_base import (  # noqa: F401
    check_page_name,
    fresh_identity,
    identity_state,
    prune_link_table,
    set_identity_state,
)
from ..infra.logsetup import note_exception as _note_exc


def keep_earlier_version(path: str, data: bytes, element: str = "", force: bool = False) -> None:
    """Keep the on-disk copy of ``path`` as an earlier version before a save replaces it."""
    try:
        from ..infra import history as H

        H.keep_version(path, data, element=element, force=force)
    except Exception:  # noqa: BLE001 -- a copy of the old bytes must never stop a save
        from ..infra import logsetup

        logsetup.note_exception("editing.save/earlier_version", page=path)


#: A page save's thumbnail when no window renders it: ``hook(doc)`` -> the TH blob, or None to
#: keep the stored one. The worker processes that save pages (Update Pages, Replace, Fix
#: pictures) install render.headless_thumb.page_thumbnail; the editor window renders its own
#: before it saves (CtDraw writes a fresh thumbnail on every save: a stale one mis-previews
#: the page in every page browser).
SAVE_THUMBNAIL = None

#: an editor save's warning when another ctgedit process held the project database for the
#: whole wait (format/projectlock)
BUSY_WARNING = (
    "The project database is busy: another ctgedit process is writing it. The "
    "page was saved. Its project database rows are written when you save it "
    "again or on the next Update Pages run."
)
#: the same for a page that has no pages.DBF row yet (a new page, a Save As to a new name):
#: Update Pages only visits registered pages, so only another save adds it
BUSY_NEW_PAGE_WARNING = (
    "The project database is busy: another ctgedit process is writing "
    "it. The page was saved but not added to its project, so it is not "
    "compiled. Save it again to add it."
)


def ctf_out_of_date(previous: bytes, ctf_path: str, resolver) -> str:
    """Why the .ctF shows an older state than this save writes ('' if it doesn't)."""
    import struct
    from ..format.container import Container
    from ..infra import logsetup
    from ..io import updatepages as UP

    try:
        c = Container.parse(previous)
        blk = c.block("page")
        if blk is None or c.word2 < 0x0C or len(blk.data) < 0x34:
            return ""
        n = struct.unpack_from("<I", blk.data, 0x1C)[0]
        raw = bytes(blk.data[0x34 : 0x34 + n * 0x60])
    except Exception:  # noqa: BLE001
        logsetup.note_exception("editing.ctf_out_of_date/head")
        return ""
    if not raw:
        return ""
    try:
        if UP.changed_genies(raw, resolver):
            return "its genies changed since"
    except Exception:  # noqa: BLE001
        logsetup.note_exception("editing.ctf_out_of_date/genies")
    try:
        mt = os.stat(ctf_path).st_mtime_ns
        # (the times only: no file read for its content tag)
        if any(x[1] > mt + 2_000_000_000 for x in UP.library_files(raw, resolver, tags=False)):
            return "a library changed since"
    except Exception:  # noqa: BLE001
        logsetup.note_exception("editing.ctf_out_of_date/libraries")
    return ""


class HistoryAndSave:
    """EditableDocument's undo / redo and save."""

    def _save_owners(self, objs):
        """The appearance owner of each record (ctf.appearance_owners) when the page holds an
        art-stripped text that the save form measures with its twin's font; None otherwise
        or when the libraries cannot be resolved."""
        if not any(
            po.type_code == 0x05 and po.raw_payload is None and not po.subtype_size & 0x2 for po in objs
        ):
            return None
        try:
            from ..format import ctf as CTF

            res = self._resolver()
            return CTF.appearance_owners(objs, res) if res is not None else None
        except Exception:  # noqa: BLE001
            from ..infra import logsetup

            logsetup.note_exception("editing.save/owners", page=self.path)
            return None

    def _save_fonts(self, objs, path: Optional[str] = None):
        """The named fonts of the page's project (legacy.named_fonts) when the page holds a
        legacy Number or String, whose Bounds the save form derives in them; None
        otherwise."""
        if not any(po.type_code in (0x1004, 0x1005) and po.raw_payload is None for po in objs):
            return None
        try:
            from ..format import legacy

            return legacy.named_fonts(path or self.path or "page.ctg")
        except Exception:  # noqa: BLE001
            from ..infra import logsetup

            logsetup.note_exception("editing.save/fonts", page=self.path)
            return None

    def _save_tag_format(self):
        """The page's own tag FORMAT lookup (EditableDocument.tag_format) for the masks of
        the legacy Numbers and Strings the save form measures, None without one."""
        return getattr(self, "tag_format", None)

    # -- the unsaved-changes flag and the saved point -------------------------------------
    # The page is unsaved when the undo history is not where it was at the last save (or
    # the open), or when it holds changes no undo step has (_dirty_other). The saved point
    # is (depth, the step on top of the undo list then; None = no step): depth counts the
    # steps the undo cap dropped too (undo_trimmed), and the step on top tells the state at
    # that depth from another one reached after undoing below it and editing (QUndoStack's
    # clean index).
    _dirty = False
    _dirty_other = False
    _saved_point = (0, None)

    @property
    def dirty(self) -> bool:
        """The page has unsaved changes."""
        return self._dirty

    @dirty.setter
    def dirty(self, value) -> None:
        value = bool(value)
        if value:
            self._dirty_other = True
        else:
            self._dirty_other = False
            self.mark_saved()
        self._dirty = value

    @property
    def history_depth(self) -> int:
        """How many steps the page is from the state it was opened in (the undo steps,
        with those the undo cap dropped)."""
        return len(self._undo) + int(getattr(self, "undo_trimmed", 0) or 0)

    def mark_saved(self) -> None:
        """The state the page is in now is the saved one (a save, an open)."""
        self._saved_point = (self.history_depth, self._undo[-1] if self._undo else None)

    @property
    def saved_depth(self) -> Optional[int]:
        """The history depth of the saved state (what the History panel badges 'Saved'),
        None when no undo or redo reaches it any more (an edit made after undoing below
        it, or the undo cap dropped the steps before it)."""
        depth, top = self._saved_point
        if depth is None:
            return None
        trimmed = int(getattr(self, "undo_trimmed", 0) or 0)
        if top is None:
            return depth if depth == trimmed else None
        k = depth - trimmed - 1  # the step's place: undo list, then redo
        undo, redo = self._undo, self._redo
        if 0 <= k < len(undo):
            return depth if undo[k] is top else None
        j = k - len(undo)
        if 0 <= j < len(redo):
            return depth if redo[-1 - j] is top else None
        return None

    def at_saved_point(self) -> bool:
        """The undo history is where it was at the last save."""
        depth, top = self._saved_point
        return (
            depth is not None
            and depth == self.history_depth
            and (self._undo[-1] if self._undo else None) is top
        )

    def _history_dirty(self) -> bool:
        return bool(self._dirty_other) or not self.at_saved_point()

    @property
    def has_unstepped_changes(self) -> bool:
        """Unsaved changes no undo step holds (a restored recovery copy, a page not written
        yet, Page Properties edits the database refused): undoing every step keeps them."""
        return bool(self._dirty_other)

    def _note_history_move(self, redone: bool, cmd) -> None:
        log = self.__dict__.setdefault("_history_moves", [])
        log.append((redone, cmd))
        del log[:-64]

    def take_history_moves(self) -> list:
        """The steps undone (False, step) and redone (True, step) since the last call,
        oldest first: what the editor window shows after Undo / Redo."""
        return self.__dict__.pop("_history_moves", None) or []

    def _push_step(self, cmd) -> None:
        """A new undo step (an edit just made): it takes the Redo list's place, so a saved
        point among the steps to redo cannot be reached any more."""
        self._undo.append(cmd)
        self._redo.clear()
        self._dirty = True
        if self._saved_point[0] is not None and self.saved_depth is None:
            self._saved_point = (None, None)  # (drops the reference to its step)

    def _saved_point_trimmed(self, dropped) -> None:
        """The undo cap drops ``dropped`` (the oldest steps): the saved state stays
        reachable when it is the state after the last of them (it becomes the oldest state
        left), not when it lies before."""
        depth, top = self._saved_point
        if depth is None:
            return
        trimmed = int(getattr(self, "undo_trimmed", 0) or 0)
        if depth < trimmed + len(dropped):
            self._saved_point = (None, None)
        elif dropped and top is dropped[-1]:
            self._saved_point = (depth, None)

    def _saved_point_merged(self, cmds, one) -> None:
        """A batch made ``cmds`` into the one step ``one``: a saved point after the last of
        them is after ``one``; one between them is gone (no undo stops there)."""
        depth, top = self._saved_point
        if depth is None or top is None:
            return
        for k, c in enumerate(cmds):
            if c is top:
                self._saved_point = (self.history_depth, one) if k == len(cmds) - 1 else (None, None)
                return

    # -- undo / redo -------------------------------------------------------------------
    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def _leave_save_point(self) -> None:
        """Undo is about to step back past a save that took the stored element as its records."""
        pts = getattr(self, "_presave_tables", None)
        if not pts:
            return
        hit = pts.get(self.history_depth)
        if hit is not None:
            pb = self.parsed.page
            pb.link_table_raw, pb.link_count = hit

    def undo(self) -> None:
        if self._undo:
            self._leave_save_point()
            cmd = self._undo.pop()
            cmd.undo()
            self._redo.append(cmd)
            # back at the saved point: nothing unsaved (unless a change is no step)
            self._dirty = self._history_dirty()
            self._note_history_move(False, cmd)
            self._note_edit()

    def redo(self) -> None:
        if self._redo:
            cmd = self._redo.pop()
            cmd.do()
            self._undo.append(cmd)
            self._dirty = self._history_dirty()
            self._note_history_move(True, cmd)
            self._note_edit()

    def history_mark(self):
        """A point in the undo history to come back to (:meth:`revert_to_mark`):
        a dialog that shows its edit on the page while it is open (Arrange >
        Align) and takes it back on Cancel."""
        # (the undo cap keeps every step made after the newest mark: _trim_undo)
        self._mark_floor = (len(self._undo), getattr(self, "undo_trimmed", 0))
        return (
            len(self._undo),
            list(self._redo),
            self.dirty,
            getattr(self, "undo_trimmed", 0),
            (self._saved_point, self._dirty_other),
        )

    def revert_to_mark(self, mark) -> None:
        """Undo every step made since ``mark`` WITHOUT offering it to Redo, and
        put the Redo list and the unsaved-changes flag back as they were: a
        cancelled preview leaves no trace in the history."""
        n, redo, dirty = mark[:3]
        # (steps the undo cap dropped since the mark shifted the history)
        n -= getattr(self, "undo_trimmed", 0) - (mark[3] if len(mark) > 3 else 0)
        while len(self._undo) > max(0, n):
            self._undo.pop().undo()
        self._mark_floor = None
        self._redo[:] = redo
        if len(mark) > 4:
            # the saved point the preview's steps hid (it was among the steps to redo)
            self._saved_point, self._dirty_other = mark[4]
        self._dirty = bool(dirty)
        self._note_edit()

    def saved_form(self, lo: int = 0, hi: Optional[int] = None) -> list:
        """Records ``lo`` to ``hi`` (whole units: a root and its members) as a save would
        write them now (genie_place.save_form: the name keys, the texts measured again),
        index for index: what 'changed since the save' compares with the file. The
        document's records are not changed."""
        pb = self.parsed.page if self.parsed is not None else None
        if pb is None:
            return []
        from ..format import genie_place as GP

        objs = pb.objects
        hi = len(objs) if hi is None else hi
        key = (getattr(self, "edit_serial", 0), getattr(self, "derive_serial", 0), len(objs))
        memo = self.__dict__.get("_saved_form_owners")
        if memo is None or memo[0] != key:
            memo = self._saved_form_owners = (key, self._save_owners(objs), self._save_fonts(objs))
        owners = memo[1]
        return list(
            GP.save_form(
                objs[lo:hi],
                owners[lo:hi] if owners is not None else None,
                fonts=memo[2],
                tag_format=self._save_tag_format(),
            )
        )

    # -- save ----------------------------------------------------------------------------
    def serialize(self) -> bytes:
        return writer.serialize_ctg(self.parsed.container, self.parsed.page, self.parsed.attributes)

    def _restore_pruned_links(self) -> None:
        """Put back the link-table entries a Save As pruned that a record uses
        again (Undo brought back a deleted genie after the Save As): the
        prune is not an undo step, and a linked record must keep its entry
        (Update Pages and CtDraw read the element stamp from it)."""
        dropped = getattr(self, "_pruned_links", None)
        pb = self.parsed.page
        if not dropped or pb is None:
            return
        from ..format import genie_place as GP

        need = GP.used_link_entries(pb.objects, dropped)
        if need:
            GP.add_link_entries(pb, need)
            self._pruned_links = [e for e in dropped if e not in need]

    def save(self, path: str | None = None, *, batch: bool = False, backup=None) -> str:
        """Write the .ctg, plus the project rows and runtime files a CtDraw save would write."""
        target = path or self.path
        stem = os.path.splitext(os.path.basename(target))[0]
        tdir = os.path.dirname(os.path.abspath(target))
        check_page_name(stem, tdir)
        from ..format import projectlock

        with projectlock.page(target):
            return self._save_locked(target, batch, backup)

    def _save_locked(self, target: str, batch: bool, backup) -> str:
        """:meth:`save` with the page's lock held."""
        from ..format import projectlock

        tdir = os.path.dirname(os.path.abspath(target))
        in_project = is_project_dir(tdir)
        name = os.path.splitext(os.path.basename(target))[0]
        src_path = self.path
        save_as = path_key(target) != path_key(src_path)
        self.rows_pending = False
        self.save_warning = ""
        if batch:
            self._batch_save_checks(target, tdir, in_project, backup)
        self._restore_pruned_links()
        # every save: a new identity (CtDraw's CoCreateGuid on each save: the .ctF
        # staleness key and the container's ts_modified) and a link table without the
        # entries nothing on the page uses any more (CtDraw rebuilds the table from the
        # records on every save), kept on the document only once the page is on disk
        self._take_save_thumbnail()
        data, copy_state, dropped, ctf_page, ctf_owners = self._serialize_save_form(target)
        previous = self._keep_previous_version(target, save_as)
        record_files: list = []
        if batch and in_project:
            # the page's pages.DBF row and staged Page Properties BEFORE the page file: a
            # refused database leaves nothing written (the page stays out of date, and
            # the next run writes them again)
            try:
                with projectlock.project(tdir):
                    record_files = self._write_page_record(target, name, save_as, src_path, raise_errors=True)
            except PermissionError as e:
                raise ProjectDatabaseInUse(
                    getattr(e, "filename", None) or os.path.join(tdir, "pages.DBF"), e.strerror or ""
                ) from e
        marked = had_marker = False
        if in_project:
            marked, had_marker = self._mark_rows_pending(target)
        with projectlock.uncancellable():
            # write ATOMICALLY: a crash / full disk mid-write must never leave a
            # truncated page (temp file in the same folder, then replace). A
            # refused write (read-only / locked page, full disk) leaves the page
            # as it was and removes the temp file.
            try:
                write_atomic(target, data)
            except BaseException:
                # (nothing was written: the marker goes, unless an earlier save left it,
                # whose rows are still behind the page file on disk)
                if marked and not had_marker:
                    from ..io import pendingrows

                    pendingrows.clear(target)
                raise
            from ..infra.paths import kill_point

            kill_point("after_ctg")
            return self._after_page_write(
                target,
                name,
                data,
                previous,
                save_as,
                src_path,
                batch,
                in_project,
                marked,
                record_files,
                copy_state,
                dropped,
                ctf_page,
                ctf_owners,
            )

    def _batch_save_checks(self, target: str, tdir: str, in_project: bool, backup) -> None:
        """An Update Pages save: refused when the file changed since it was opened or the
        project database cannot be written; the page backed up first (``backup`` =
        (folder, reason))."""
        ident = getattr(self, "_open_identity", None)
        if ident is not None and file_identity(target) != ident:
            raise PageChangedOnDisk(target)
        if in_project:
            from ..io import pgregister

            pgregister.probe_writable(tdir)
        if backup:
            from ..io import updatepages

            updatepages.backup_page(target, backup[0], backup[1])

    def _take_save_thumbnail(self) -> None:
        """The page's thumbnail drawn by the SAVE_THUMBNAIL hook, when one is set."""
        hook = SAVE_THUMBNAIL
        if hook is not None and self.parsed.page is not None:
            try:
                blob = hook(self)
                if blob:
                    self.parsed.page.thumbnail = blob
            except Exception:  # noqa: BLE001 -- the stored thumbnail then
                from ..infra import logsetup

                logsetup.note_exception("history.HistoryAndSave._take_save_thumbnail", page=self.path)

    def _serialize_save_form(self, target: str) -> tuple:
        """The page's bytes with a new identity and a pruned link table: (the bytes, the
        identity state they carry, the link entries dropped, the page block and owners
        the .ctF is built from). The document keeps its own identity and records."""
        old_state = identity_state(self.parsed)
        fresh_identity(
            self.parsed, created=not os.path.exists(target) or bool(getattr(self, "_creating", False))
        )
        dropped = prune_link_table(self.parsed.page)
        copy_state = identity_state(self.parsed)
        # a named record carries its name as '__AN_NAME__' too and a text its extent as
        # measured now, as CtDraw's load + save writes them (genie_place.save_form): in the
        # file only, the document's records stay
        from ..format import genie_place as GP

        pb = self.parsed.page
        objs0 = pb.objects if pb is not None else None
        saved, owners0 = objs0, None
        try:
            if objs0 is not None:
                owners0 = self._save_owners(objs0)
                saved = GP.save_form(
                    objs0, owners0, fonts=self._save_fonts(objs0, target), tag_format=self._save_tag_format()
                )
                pb.objects = saved
            data = self.serialize()
        finally:
            if objs0 is not None:
                pb.objects = objs0
            if copy_state is not None:
                set_identity_state(self.parsed, old_state)
        # the page block of these bytes for the .ctF: the records the file holds (the save
        # form), the owners of the save form's own records with them (a record's owner is
        # itself or a library record, and the save form only remeasures and renames)
        ctf_page = pb
        if pb is not None and saved is not objs0:
            import copy as _copy

            ctf_page = _copy.copy(pb)
            ctf_page.objects = saved
        ctf_owners = None
        if owners0 is not None and objs0 is not None and len(owners0) == len(saved):
            ctf_owners = [saved[i] if ow is objs0[i] else ow for i, ow in enumerate(owners0)]
        return data, copy_state, dropped, ctf_page, ctf_owners

    def _keep_previous_version(self, target: str, save_as: bool) -> Optional[bytes]:
        """The page's bytes on disk now (None for a new file), kept as an earlier version
        (File > Page History)."""
        # the page as it is on disk now (paired with its .ctF when CtDraw wrote
        # both): the per-page check of the native .ctF writer
        try:
            with open(target, "rb") as fh:
                previous = fh.read()
        except OSError:
            previous = None
        # a fast-display check process still reading this page / its .ctF
        # ends first: Windows cannot replace or remove a file another process
        # holds open (its verdict is the one this save then uses)
        if not save_as:
            self._settle_ctf_precheck()
        # the page as it was on disk goes into its earlier versions (File > Page History)
        # first: a copy of those bytes, which never changes what is written
        if previous is not None:
            # (the first save after File > Page History > Restore keeps what it replaces
            # whatever Settings 'New earlier version at most every' says)
            keep_earlier_version(target, previous, force=getattr(self, "restored_version", None) is not None)
            self.restored_version = None
        return previous

    @staticmethod
    def _mark_rows_pending(target: str) -> tuple:
        """Mark the page's project rows as behind its file; (marked, an earlier save had
        marked them)."""
        # the rows fall behind the page file from here until the register has run
        from ..io import pendingrows

        marked = False
        had_marker = pendingrows.marked(target) is not None
        try:
            pendingrows.mark(target)
            marked = True
        except OSError:
            # (ctgedit's data folder cannot be written: the save goes on without a
            # marker)
            from ..infra import logsetup

            logsetup.note_exception("history.HistoryAndSave._mark_rows_pending", page=target)
        return marked, had_marker

    def _after_page_write(
        self,
        target,
        name,
        data,
        previous,
        save_as,
        src_path,
        batch,
        in_project,
        marked,
        record_files,
        copy_state,
        dropped,
        ctf_page,
        ctf_owners,
    ) -> str:
        """:meth:`save` once the page file is written: the document's state, the
        registration (editor saves), the fast display and the project database rows."""
        self._creating = False
        # CtDraw's save bakes what it writes: the half pixels of top-level records and the
        # turned boxes of texts are gone after it, a carrier's members keep theirs, every
        # Local stays with its zero signs and a plain group's Local is composed into its
        # carriers (GeometryEdits._turn_memo_after_save); with Settings > Editing's exact
        # quarter turns all of it is kept
        if not getattr(type(self), "REVERSIBLE_TURNS", False):
            self._turn_memo_after_save()
        self.save_files = [target] + [p for p in record_files if p != target]
        if copy_state is not None:
            set_identity_state(self.parsed, copy_state)
            self._pruned_links = list(getattr(self, "_pruned_links", None) or []) + dropped
        self.path = target  # Save As: track the file now on disk
        self._disk_stamp = self.disk_stamp(target)
        self._open_identity = file_identity(target)
        self.read_only = False
        self.damage = ()  # the file on disk is whole again
        if not (batch and in_project):
            # register brand-new pages in the project DB, as CtDraw's save does (an
            # unregistered page never compiles). No-op when the target dir is not a
            # project (no pages.DBF) or the page is already registered.
            files = self._write_page_record(target, name, save_as, src_path, raise_errors=False)
            self.save_files += [p for p in files if p not in self.save_files]
        # Page Properties edits the database refused stay staged: the page is
        # saved, but these changes are not -- closing it still asks
        unsaved_props = bool(self.staged_page_state())
        if unsaved_props:
            self.save_warning = "\n".join(
                filter(
                    None,
                    (
                        self.save_warning,
                        "The Page Properties changes (title, area, events, keyboard commands) were "
                        "NOT written to the project database. Save again once it can be written.",
                    ),
                )
            )
        self.save_report = self._update_runtime_files(
            target, data, previous, page=ctf_page, owners=ctf_owners
        )
        if marked:
            from ..io import pendingrows

            # (the marker is about the rows only: a stale .ctF that could not be removed
            # is in save_warning)
            if getattr(self, "_rows_ok", True):
                pendingrows.clear(target)
            else:
                self.rows_pending = True
        # the saved point is here now; Page Properties edits the database refused are
        # unsaved changes no undo step holds
        self.mark_saved()
        self._dirty_other = unsaved_props
        self._dirty = unsaved_props
        from ..infra import logsetup

        logsetup.files_written(
            "saved page %s%s" % (target, " (Save As)" if save_as else ""), self.save_files, self.save_report
        )
        if self.save_warning:
            logsetup.get("save").warning("%s: %s", os.path.basename(target), self.save_warning)
        return target

    def _write_page_record(
        self, target: str, name: str, save_as: bool, src_path: str, raise_errors: bool
    ) -> list:
        """Write the page's pages.DBF row and staged page property edits under the project lock."""
        from ..format import journal, projectlock
        from ..io.project import Project

        pdir = os.path.dirname(os.path.abspath(target))
        try:
            proj = Project(pdir)
            copied = False
            keys_from = None
            # one journaled unit: a failure half-way leaves pages.DBF / pgkey
            # as they were
            with projectlock.project(pdir), journal.transaction("register page %s" % name) as txn:
                if save_as:
                    # Save As keeps the page's PROPERTIES: its pages.DBF row (title,
                    # area, events -- a copied SA master page must still run
                    # Workspace_Init()) and its page-wide keyboard commands, which
                    # live only in pgkey.DBF
                    src_dir = os.path.dirname(os.path.abspath(src_path))
                    src_name = os.path.splitext(os.path.basename(src_path))[0]
                    copied = proj.copy_page_record(src_dir, src_name, name)
                    if copied:
                        keys_from = (src_dir, src_name)
                if not copied and proj.page_record(name) is None:
                    proj.add_page_record(name)
                # the Page Properties edits made since the last save (staged
                # in the document, like CtDraw's in-memory page properties)
                if proj.has_page_table():
                    self._flush_page_record(proj, name, copy_keys_from=keys_from)
                elif self.staged_page_state():
                    # a Save As out of the project: there is no database to
                    # take them, and the page no longer belongs to one
                    self.save_warning = (
                        "The Page Properties changes (title, area, events, keyboard "
                        "commands and so on) were not stored, because %s is not a project "
                        "folder (it has no pages.DBF)." % pdir
                    )
            # written: nothing is staged any more (an Undo stages the old
            # values again, and the next save writes them)
            self._page_rec_staged = {}
            self._page_keys_staged = None
            return list(txn.written)
        except Exception as e:  # noqa: BLE001 -- registration must never block a save
            if raise_errors:
                raise
            if isinstance(e, PermissionError):
                self.save_warning = (
                    "pages.DBF is locked (is Plant SCADA Studio open?). The "
                    "page was saved but not registered; save again to "
                    "register it."
                )
            elif isinstance(e, ProjectBusyError):
                from ..io import pendingrows

                # (a page with no row yet is not part of the project: no Update Pages run
                # writes its rows, only the next save adds it)
                self.save_warning = (
                    BUSY_WARNING if pendingrows.registered(target) is not False else BUSY_NEW_PAGE_WARNING
                )
            elif isinstance(e, PageRecordError):
                self.save_warning = (
                    "The page was saved, but its project database rows were left as they were: %s." % e
                )
            else:
                self.save_warning = (
                    "The page was saved but not registered (%s). pages.DBF "
                    "was left as it was; save again to register the page." % e
                )
            return []

    def _update_runtime_files(
        self, target: str, data: bytes, previous: Optional[bytes], page=None, owners=None
    ) -> str:
        """Update the files the runtime and compiler read, as CtDraw's save does."""
        notes = []
        self._rows_ok, self._ctf_ok = True, True
        pdir = os.path.dirname(os.path.abspath(target))
        is_project = is_project_dir(pdir)
        if not is_project:
            return ""
        ctf_path = target[:-1] + "F" if target.lower().endswith(".ctg") else None
        if ctf_path:
            self._update_fast_display(target, ctf_path, data, previous, page, owners, notes)
        self._register_rows(pdir, target, notes)
        return "; ".join(notes)

    def _ctf_trusted(self, target: str, previous, existing: list, old_ctf, res, notes: list) -> bool:
        """Whether the native .ctF writer may write this page's .ctF (else a stale one is
        removed and the runtime reads the .ctg)."""
        from ..format import ctf as CTF

        # trusted = the writer reproduces CtDraw's .ctF of this page's
        # previous version byte for byte -- or the .ctF on disk is the
        # one this document wrote (no need to rebuild the old one)
        mine = getattr(self, "_ctf_stamp", None)
        trusted = bool(existing) and mine is not None and self.disk_stamp(existing[0]) == mine
        if not trusted and previous is not None and old_ctf is not None:
            pre = self._ctf_precheck_result(previous, old_ctf)
            # (equal up to the stamps of CtDraw's .ctF write: ctf_matches)
            trusted = (
                pre
                if pre is not None
                else ctf_matches(CTF.build_ctf(previous, target, resolver=res), old_ctf)
            )
        if (
            not trusted
            and previous is not None
            and old_ctf is not None
            and os.environ.get("CTG_CTF_REMOVE_STALE") != "1"
        ):
            why = ctf_out_of_date(previous, existing[0], res)
            if why:
                # the old .ctF shows what the page was before its genies or
                # libraries changed: CtDraw's save writes a new one, and the
                # writer reproduces those
                trusted = True
                notes.append("fast display was out of date (%s)" % why)
        if not trusted and not existing and previous is None:
            # a page this document CREATES whose .ctF the native
            # writer is known to reproduce (a super genie page)
            trusted = bool(getattr(self, "_ctf_new_page", False))
        return trusted

    def _update_fast_display(
        self, target: str, ctf_path: str, data: bytes, previous: Optional[bytes], page, owners, notes: list
    ) -> None:
        """Write the page's .ctF (fast display), or remove a stale one."""
        try:
            from ..io.citect_config import fast_display_enabled

            fast = fast_display_enabled()
        except Exception:  # noqa: BLE001 -- swallow-ok: no install or an unreadable ini: the default
            fast = True
        # one file on Windows ('.ctF' and '.ctf' are the same name there)
        existing, seen = [], set()
        for p in (ctf_path, target[:-1] + "f"):
            key = path_key(p)
            if key not in seen and os.path.exists(p):
                seen.add(key)
                existing.append(p)
        old_ctf = None
        if existing:
            try:
                with open(existing[0], "rb") as fh:
                    old_ctf = fh.read()
            except OSError:
                old_ctf = None
        wrote = False
        if fast:
            try:
                from ..format import ctf as CTF

                res = self._resolver()
                trusted = self._ctf_trusted(target, previous, existing, old_ctf, res, notes)
                if trusted:
                    blob = CTF.build_ctf(data, target, resolver=res, page=page, owners=owners)
                    out = existing[0] if existing else ctf_path
                    write_atomic(out, blob)
                    self._ctf_stamp = self.disk_stamp(out)
                    wrote = True
                    notes.append("fast display rebuilt")
                    self._saved_file(out)
            except ImportError:
                pass
            except Exception as e:  # noqa: BLE001
                _note_exc("history.HistoryAndSave._update_fast_display", path=self.path)
                notes.append("fast display not rebuilt (%s)" % e)
        # the trust check was about the page before this save: done with it
        self.stop_ctf_precheck()
        if not wrote:
            for p in existing:
                try:
                    os.remove(p)
                    notes.append("stale fast display removed (runtime reads the .ctg)")
                    self._saved_file("removed " + p)
                except OSError as e:
                    self._ctf_ok = False
                    self.save_warning = (self.save_warning + "\n" if self.save_warning else "") + (
                        "could not remove the stale %s (%s): the "
                        "runtime keeps showing the old page" % (os.path.basename(p), e)
                    )

    def _register_rows(self, pdir: str, target: str, notes: list) -> None:
        """Write the page's project database rows (io/pgregister.register_page)."""
        try:
            from ..io import pgregister

            if not hasattr(pgregister, "register_page"):
                raise ImportError("pgregister.register_page")
            name = os.path.splitext(os.path.basename(target))[0]
            res = pgregister.register_page(pdir, name, self.parsed, resolver=self._resolver())
            notes.append(
                "project database updated%s" % (" (%s)" % res if isinstance(res, str) and res else "")
            )
            if isinstance(res, dict):
                for p in res.get("files") or ():
                    self._saved_file(p)
                changed = {
                    t: v
                    for t, v in (res.get("tables") or {}).items()
                    if isinstance(v, dict) and v.get("action") == "updated"
                }
                if changed:
                    from ..infra import logsetup

                    logsetup.get("register").info(
                        "%s: %s",
                        name,
                        ", ".join(
                            "%s %d updated / %d deleted / %d appended"
                            % (
                                t,
                                len(v.get("updated", ())),
                                len(v.get("deleted", ())),
                                len(v.get("appended", ())),
                            )
                            for t, v in changed.items()
                        ),
                    )
        except ImportError:
            pass
        except PermissionError as e:
            self._rows_ok = False
            self.save_warning = (self.save_warning + "\n" if self.save_warning else "") + (
                "The project database is locked (%s). A compile will not see this save's "
                "animation changes; its tables were left as they were." % e
            )
        except ProjectBusyError:
            self._rows_ok = False
            if BUSY_WARNING not in self.save_warning and BUSY_NEW_PAGE_WARNING not in self.save_warning:
                self.save_warning = (self.save_warning + "\n" if self.save_warning else "") + BUSY_WARNING
        except Exception as e:  # noqa: BLE001
            _note_exc("history.HistoryAndSave._register_rows", path=self.path)
            self._rows_ok = False
            self.save_warning = (self.save_warning + "\n" if self.save_warning else "") + (
                "The project database could not be updated (%s). Its tables were left as "
                "they were before this save; save again to retry." % e
            )

    def _saved_file(self, path: str) -> None:
        files = getattr(self, "save_files", None)
        if files is None:
            files = self.save_files = []
        if path not in files:
            files.append(path)
