"""Update a page's genies in a separate process."""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import sys
import tempfile
from typing import List, Optional

#: the request / answer format (both sides check it)
FORMAT = "ctgedit-genie-update/1"


def helper_waits() -> None:
    """A helper process's lock waits for a project database, a page and the composition
    cache: the editor's 'Helper processes wait' setting, passed down in CTG_HELPER_WAIT
    (app.editor_waits), else format/projectlock.HELPER_WAITS. Longer than the editor's,
    so a busy project fails a page only after a real hang."""
    from ..format import composer_store, projectlock

    try:
        wait = float(os.environ.get("CTG_HELPER_WAIT") or 0)
    except ValueError:  # swallow-ok: a garbled value takes the default
        wait = 0.0
    project, page = (wait, wait) if wait > 0 else projectlock.HELPER_WAITS
    projectlock.set_waits(project, page)
    composer_store.set_lock_wait(project)


# One read of each library per request (format/symbols.held_libraries, 'one
# read per composition'): what changed, the composition and the stamps the answer's link
# table quotes come from the same version of each library, also when the editor or the
# chain's helper saves one meanwhile; an answer is kept under the files' key
# (infra/answer_memo) only when none of them was written since it was read (_read_changed).
def _one_read(fn):
    """``fn`` (a request's composition) inside format/symbols.held_libraries."""
    import functools

    @functools.wraps(fn)
    def run(*a, **k):
        from ..format.symbols import held_libraries

        with held_libraries():
            return fn(*a, **k)

    return run


def _read_changed() -> bool:
    """A library the current request read was written since (symbols.LibraryHold.changed):
    what it composed is not what the files hold now."""
    from ..format.symbols import current_hold

    hold = current_hold()
    return hold is not None and bool(hold.changed())


def _sha(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def record_ops(before: List[bytes], after: List[bytes]) -> list:
    """[(i1, i2, [old sha1], [new payload], anchor)]: the ranges of ``before`` that
    ``after`` replaces (difflib over the payloads); ``anchor`` = the sha1 of the record
    before the range (None at the start), so an insertion can be found again too."""
    sm = difflib.SequenceMatcher(None, [_sha(b) for b in before], [_sha(b) for b in after], autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        ops.append(
            (i1, i2, [_sha(b) for b in before[i1:i2]], after[j1:j2], _sha(before[i1 - 1]) if i1 else None)
        )
    return ops


def root_signature(po) -> str:
    """What tells a genie root of the request's page from another one at the same handle
    (:func:`handle_facts`): the start of its payload's sha1."""
    from ..format import writer

    return _sha(writer.object_payload(po))[:16]


def handle_facts(objs) -> tuple:
    """(the handles of the records ``objs``, {handle: :func:`root_signature`} of their genie
    roots): taken before an update, for :func:`answer_facts`."""
    from ..format import geometry as G

    had = {G.get_handle(p) for p in objs}
    roots = {
        G.get_handle(p): root_signature(p) for p in objs if p.class_flag and (p.subtype_size & 0x201) == 0x201
    }
    had.discard(None)
    roots.pop(None, None)
    return had, roots


def genie_copy_spans(objs) -> list:
    """[(root index, subtree end, 'lib.elem.<ext' lower case)] of the genie copies (linked
    roots) among the records ``objs``: taken before an update, for
    :func:`composed_references`."""
    from ..format.forest import subtree_end
    from ..format.symbols import reference_from_object

    out = []
    for r, p in enumerate(objs):
        if not p.class_flag or (p.subtype_size & 0x201) != 0x201:
            continue
        try:
            ref = reference_from_object(p)
        except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link names nothing
            continue
        if ref is not None:
            out.append((r, subtree_end(objs, r), ("%s.%s.<%s" % (ref.library, ref.element, ref.ext)).lower()))
    return out


def composed_references(keys, spans=(), ops=()) -> list:
    """'lib.elem.<ext' of the genies an update recomposes."""
    out = set()
    items = keys.items() if isinstance(keys, dict) else ((k, None) for k in keys or ())
    for k, nested in items:
        for lib, el in [k] + list(nested or ()):
            out.add(("%s.%s.<ctm" % (lib, el)).lower())
    ranges = [(op[0], op[1]) for op in ops or ()]
    for r, end, ref in spans or ():
        if ref not in out and any((i1 < end and i2 > r) if i2 > i1 else r < i1 < end for i1, i2 in ranges):
            out.add(ref)
    return sorted(out)


def answer_facts(before: tuple, after, composed=()) -> dict:
    """Facts about the page an answer was computed from, so it can be applied after edits."""
    from ..format import geometry as G

    had, roots = before
    now = {G.get_handle(p) for p in after}
    return {
        "fresh": sorted(h for h in now - had if h is not None),
        "roots": sorted([h, sig] for h, sig in roots.items()),
        "composed": list(composed or ()),
    }


@_one_read
def compute(
    page_path: str,
    data: bytes,
    changes: Optional[dict] = None,
    bindings: Optional[dict] = None,
    template: bool = False,
    page_sha1: Optional[str] = None,
    previous: Optional[dict] = None,
) -> dict:
    """Compute the genie update of a page."""
    from ..editing import EditableDocument
    from ..format import writer

    # (the records' bags are scanned where one is read: the composition reads bodies, and
    # the answer is payloads)
    doc = EditableDocument.from_bytes(data, page_path, records_only=True, lazy_bags=True)
    if page_sha1:
        doc._open_sha1 = bytes.fromhex(page_sha1)
    if changes is None and not template and not previous:
        changes = doc.genie_changes()
    changes = changes or {}
    # (the key the window looks the answer up by: the page's bytes, the changes, the files;
    # not for a genie save's update by its previous version, which composes less)
    memo_key = (
        doc.genie_noop_key(changes)
        if bindings is None and changes and not template and not previous
        else None
    )
    before = [writer.object_payload(p) for p in doc.parsed.page.objects]
    facts = handle_facts(doc.parsed.page.objects)
    link_before = doc.parsed.page.link_table_raw, doc.parsed.page.link_count
    rep: dict = {}
    if template:
        n = int(doc.update_template(page_properties=False) or 0)
    elif bindings is not None:
        # (a bindings job is the genie dialog's OK on a big genie: ui.genie_jobs)
        n = int(
            bool(
                doc.set_genie_bindings(
                    int(bindings["index"]), dict(bindings.get("values") or {}), dialog=True
                )
            )
        )
    else:
        n = apply_update(doc, changes, previous, report=rep) if changes or previous else 0
    after = [writer.object_payload(p) for p in doc.parsed.page.objects]
    pb = doc.parsed.page
    out = {
        "format": FORMAT,
        "updated": n,
        "ops": [
            [i1, i2, olds, [b.hex() for b in news], anchor]
            for i1, i2, olds, news, anchor in record_ops(before, after)
        ],
    }
    if previous and not template and bindings is None:
        out["left"] = rep.get("left", 0)
    # (the genies it composed again: a genie update's, its changes name the placed genies;
    # a template's step and a genie dialog's OK compose no copy the user could have added
    # meanwhile)
    composed = {**{k: None for k in previous or {}}, **changes}
    out.update(
        answer_facts(
            facts, pb.objects, composed_references(composed) if not template and bindings is None else ()
        )
    )
    if getattr(doc, "genie_update_left", None) or getattr(doc, "genie_update_unfinished", False):
        out["unfinished"] = True
    if (pb.link_table_raw, pb.link_count) != link_before:
        out["link_table"] = [
            (pb.link_table_raw or b"").hex(),
            pb.link_count,
            (link_before[0] or b"").hex(),
            link_before[1],
        ]
    # (not an answer that left an instance as stored, its library unreadable then or a
    # composition that failed: the next open composes it again)
    if (
        memo_key is not None
        and n
        and (out["ops"] or "link_table" in out)
        and not out.get("unfinished")
        and not _read_changed()
    ):
        from ..infra import answer_memo

        answer_memo.put(memo_key, out)
    return out


@_one_read
def compute_element(req: dict, data: bytes) -> dict:
    """Compute the nested genie update of an element open in a tab."""
    from ..editing import ElementChangedMeanwhile, ElementDocument

    try:
        doc = ElementDocument.for_nested_update(req, data)
    except ElementChangedMeanwhile:
        # saved since the tab read it (its own save, or the chain's): that save composed
        # the nested genies; the tab reads the library again (reload or the disk check)
        return {"format": FORMAT, "updated": 0, "ops": [], "changed_on_disk": True}
    report: dict = {}
    ops, link = doc.nested_update_ops(report=report)
    out = {
        "format": FORMAT,
        "updated": len(ops),
        "ops": [[i1, i2, olds, [b.hex() for b in news], anchor] for i1, i2, olds, news, anchor in ops],
    }
    # (the handles its new records took and the copies the tab held: answer_facts)
    out.update(report.get("answer_facts") or {})
    if link is not None:
        out["link_table"] = [link[0].hex(), link[1], link[2].hex(), link[3]]
    return out


def compute_save(req: dict, data: bytes) -> dict:
    """Compute the save normalisation of a large element."""
    from ..editing import ElementChangedMeanwhile, ElementDocument

    try:
        doc = ElementDocument.for_nested_update(req, data)
    except ElementChangedMeanwhile:
        return {"format": FORMAT, "changed_on_disk": True}
    save = req.get("save") or {}
    doc._instantiated = {str(x) for x in save.get("instantiated") or ()}
    draw = None
    if save.get("thumbnail"):
        # (the save brings a nested copy up to date: the thumbnail it stores is drawn here
        # too, from the element as stored, as the window's save draws it)
        from ..render import headless_thumb

        draw = headless_thumb.element_thumbnail
    return {
        "format": FORMAT,
        "memo": doc.content_memo(
            bool(save.get("refresh")), bool(save.get("every_nested")), thumbnail_for=draw
        ),
    }


def apply_update(doc, changes: dict, previous: Optional[dict] = None, report: Optional[dict] = None) -> int:
    """Apply a page's genie update."""
    n = 0
    if previous:
        from ..editing import ElementDocument

        try:
            stale = {(a.lower(), b.lower()) for a, b in (doc.genie_changes() or {})}
        except Exception:  # noqa: BLE001 -- each version is tried, as before
            from ..infra import logsetup

            logsetup.note_exception("genie_worker.apply_update/changes", page=getattr(doc, "path", ""))
            stale = None
        prev = {}
        for (lib, el), (lib_path, copy_path) in previous.items():
            if stale is not None and (lib.lower(), el.lower()) not in stale:
                # (the page already quotes the genie's current stamp: an update that ran
                # meanwhile has brought its copies up to date)
                continue
            got = ElementDocument.previous_version(lib_path, el, copy_path)
            if got is not None:
                prev[(lib, el)] = got
        if prev:
            n += doc.update_genie_instances(set(prev), prev) or 0
            if report is not None:
                report["left"] = int(getattr(doc, "genie_update_skipped", 0) or 0)
    if changes:
        n += doc.apply_genie_changes(changes) or 0
    return n


def previous_to_json(previous: Optional[dict]) -> list:
    return [[k[0], k[1], p[0], p[1]] for k, p in (previous or {}).items()]


def previous_from_json(rows) -> dict:
    return {(a, b): (lib, copy) for a, b, lib, copy in rows or ()}


def changes_to_json(changes: dict) -> list:
    return [[k[0], k[1], None if v is None else sorted([list(c) for c in v])] for k, v in changes.items()]


def changes_from_json(rows) -> dict:
    return {(a, b): (None if v is None else frozenset(tuple(c) for c in v)) for a, b, v in rows}


def write_request(
    folder: str,
    page_path: str,
    data: bytes,
    changes: dict,
    bindings: Optional[dict] = None,
    template: bool = False,
    element: Optional[dict] = None,
    page_sha1: Optional[str] = None,
    previous: Optional[dict] = None,
) -> str:
    """The request file (JSON + the page bytes next to it); its path. ``element`` = an
    element tab's request (ElementDocument.nested_update_request): :func:`compute_element`
    answers it (with a 'save' part, ElementDocument.content_memo_request:
    :func:`compute_save`). ``page_sha1``, ``previous``: :func:`compute`'s."""
    os.makedirs(folder, exist_ok=True)
    fd, req = tempfile.mkstemp(prefix="%d-" % os.getpid(), suffix=".json", dir=folder)
    os.close(fd)
    with open(req + ".page", "wb") as fh:
        fh.write(data)
    from ..format.fileio import library_changes

    with open(req, "w", encoding="utf-8") as fh:
        # ("file_changes": the libraries the editor wrote, fileio.adopt_changes)
        json.dump(
            {
                "format": FORMAT,
                "page": page_path,
                "changes": changes_to_json(changes or {}),
                "bindings": bindings,
                "template": bool(template),
                "element": element,
                "page_sha1": page_sha1,
                "previous": previous_to_json(previous),
                "file_changes": library_changes(),
            },
            fh,
        )
    return req


#: the report keys of the library elements a save's chain saved (update_closed_pages);
#: ``not_composed`` = [label, ['lib.genie', ...]]: an element saved whose copies of those
#: nested genies could not be composed again (they stay as stored, under their old stamp)
CHAIN_FIELDS = (
    "elements_saved",
    "elements_in_editor",
    "elements_current",
    "elements_skipped",
    "elements_failed",
    "super_pages",
    "tabs_saved",
    "not_composed",
    "element_versions",
)

#: the partial answer beside the progress file (update_closed_pages): what was saved so far,
#: rewritten after each element, before the pages and on an error of the pages phase (the
#: pages done are in the records file, :data:`RECORDS_SUFFIX`), so a helper that dies still
#: tells the editor (chain_partial_answer)
GENIES_SUFFIX = ".genies.json"
#: the pages phase's records beside the progress file (update_closed_pages: a JSON line per
#: page as it is done, io/updatepages.page_record): the editor's progress figures count them
RECORDS_SUFFIX = ".records"


def update_closed_pages(
    lib_path: str,
    element: str,
    skip=(),
    builder_open=(),
    backup: Optional[str] = None,
    progress_file: str = "",
    skip_file: str = "",
    cancel_file: str = "",
    editor_file: str = "",
    elements: bool = True,
) -> dict:
    """Update everything that uses the saved element and is out of date."""
    from ..io import genieusage as GU
    from ..render import headless_thumb

    usage = GU.element_users(lib_path, element)
    stopped: list = []
    out = {
        "format": FORMAT,
        "pages": [],
        "updated": [],
        "failed": [],
        "open_in_builder": [],
        "unregistered": [],
        "open_at_start": [],
        "after_saves": [],
        "problems": list(usage.problems),
        "unreadable": [list(x) for x in usage.unreadable],
    }
    for k in CHAIN_FIELDS:
        out[k] = []
    if elements and any(u.ref.kind == "element" and u.needs_update for u in usage.users):
        plan = GU.update_plan(usage)
        # (each element's kind: the editor's result counts them so, a template or a symbol is
        # not called a genie)
        out["element_kinds"] = {
            u.ref.label: u.ref.what for u in list(plan.elements) + [u for u, _w in plan.skipped]
        }
        eprogress, _skip = _progress_and_skip(progress_file, "", cancel_file, stopped, phase="genies")

        def partial(rep):
            # (after each element: a helper that dies later still tells the editor what it
            # saved, its tabs to read again and the backup)
            if progress_file:
                _write_json(progress_file + GENIES_SUFFIX, _genies_answer(out, rep, backup))

        # (a super genie page a genie's save creates gets its thumbnail rendered here, as the
        # editor's own saves render it)
        erep = GU.run_update(
            GU.UpdatePlan(plan.elements, [], plan.skipped),
            backup=backup,
            editor_state=_editor_state(editor_file),
            progress=eprogress,
            page_thumbnail=headless_thumb.page_thumbnail,
            element_thumbnail=headless_thumb.element_thumbnail,
            on_element=partial,
        )
        _genies_answer(out, erep, backup)
        if progress_file:
            # (the editor reads it when this helper gives no answer: the genies it saved)
            _write_json(progress_file + GENIES_SUFFIX, out)
        if erep.stopped or stopped:
            out["stopped"] = True
            return out
    try:
        return _closed_pages_phase(
            out, usage, skip, builder_open, backup, progress_file, skip_file, cancel_file, stopped
        )
    except Exception as e:  # noqa: BLE001 -- the genies phase's answer is kept beside it
        import traceback
        from ..infra import logsetup

        logsetup.note_exception("genie_worker.update_closed_pages/pages")
        out.update(error="%s: %s" % (type(e).__name__, e), trace=traceback.format_exc()[-4000:])
        return out


def _genies_answer(out: dict, erep, backup) -> dict:
    """``out`` (update_closed_pages' answer) with the genies phase's keys from its report
    ``erep`` (genieusage.UpdateUsersReport: also part way through, after each element)."""
    out.update(
        libraries=list(erep.libraries_written),
        elements_saved=list(erep.elements_saved),
        elements_in_editor=list(erep.elements_in_editor),
        elements_current=list(erep.elements_current),
        elements_skipped=list(erep.skipped),
        elements_failed=list(erep.failed),
        super_pages=list(erep.super_pages),
        save_warning=erep.save_warning,
        tabs_saved=[list(t) for t in erep.tabs_saved],
        # (what it read and stored of each element: an open tab holding what it read
        # takes the stored block as its own, never 'changed by another program')
        element_versions=[list(t) for t in erep.element_versions],
        not_composed=[[str(lb), list(keys)] for lb, keys in erep.not_composed],
        backup=backup if (erep.elements_saved or erep.elements_in_editor) else None,
    )
    return out


def _pages_answer(out: dict, rep, pages, stopped: bool, backup, partial: bool = False, label=None) -> dict:
    """Add the pages phase's keys to the answer."""
    if label is None:
        from ..io import updatepages

        label = updatepages.same_name_label(pages)
    if partial:
        bk = out.get("backup") or (backup if rep.updated else None)
        unregistered = list(rep.unregistered) or list(out.get("unregistered") or ())
        recovered = list(rep.recovered) or list(out.get("recovered") or ())
    else:
        bk = rep.backup or backup
        unregistered, recovered = list(rep.unregistered), list(rep.recovered)
    out.update(
        {
            "pages": list(pages),
            "updated": list(rep.updated),
            "unregistered": unregistered,
            "templates": int(rep.templates),
            "supergenies": int(rep.supergenies),
            "placeholders": int(getattr(rep, "placeholders", 0) or 0),
            "skipped_open_paths": [
                r.get("path") for r in rep.records if r.get("outcome") == "skipped_open" and r.get("path")
            ],
            # (the file versions it read and wrote of each page it updated: a tab that
            # holds what it read takes the file as its own: page_versions)
            "page_versions": page_versions(rep.records),
            "failed": list(rep.failed),
            "open_in_builder": list(rep.open_in_builder),
            "skipped_open": list(rep.skipped_open),
            "changed_meanwhile": list(rep.changed_meanwhile),
            "db_in_use": list(rep.db_in_use),
            "rows_pending": list(rep.rows_pending),
            "links_dropped": list(rep.links_dropped),
            "unchanged": list(rep.unchanged),
            "details": {
                label(r): r.get("detail") or "" for r in rep.records if r.get("outcome") == "unchanged"
            },
            "stopped": bool(stopped) or bool(rep.not_checked),
            "backup": bk,
            "recovered": recovered,
        }
    )
    return out


def page_versions(records) -> list:
    """[page file, the (mtime_ns, size) it read, the one it wrote] of each page of the
    records (io/updatepages.page_record) that was updated and says both."""
    out = []
    for r in records or ():
        if r.get("outcome") == "updated" and r.get("path") and r.get("read") and r.get("wrote"):
            out.append([r["path"], list(r["read"]), list(r["wrote"])])
    return out


def chain_answer_so_far(progress_file: str) -> dict:
    """The partial answer of a running chain."""
    out: dict = {}
    if not progress_file:
        return out
    try:
        with open(progress_file + GENIES_SUFFIX, encoding="utf-8") as fh:
            got = json.load(fh)
        if isinstance(got, dict):
            out = got
    except OSError, ValueError:  # swallow-ok: none written yet (or being replaced)
        pass
    recs = page_versions(_streamed_records(progress_file + RECORDS_SUFFIX))
    if recs:
        out = dict(out, page_versions=list(out.get("page_versions") or ()) + recs)
    return out


def _streamed_records(path: str) -> list:
    """The complete JSON lines of a records file (:data:`RECORDS_SUFFIX`; a line cut short
    by a helper that died is left out)."""
    out: list = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.endswith("\n"):
                    break
                with contextlib.suppress(ValueError):
                    rec = json.loads(line)
                    if isinstance(rec, dict) and "i" in rec:
                        out.append(rec)
    except OSError:  # swallow-ok: no page was done (no file)
        return out
    return out


def chain_partial_answer(res: dict, progress_file: str, backup: Optional[str] = None) -> dict:
    """Rebuild a chain's answer from what it wrote as it went."""
    if not res.get("error"):
        return res
    out = dict(res)
    if "elements_saved" not in out and progress_file:
        try:
            with open(progress_file + GENIES_SUFFIX, encoding="utf-8") as fh:
                part = json.load(fh)
        except OSError, ValueError:  # swallow-ok: none written (it died before)
            part = None
        if isinstance(part, dict):
            out = dict(part, **{k: res[k] for k in ("error", "trace") if k in res})
    recs = _streamed_records(progress_file + RECORDS_SUFFIX) if progress_file else []
    if recs:
        from ..io import updatepages

        pages = list(out.get("pages") or ()) or [r.get("path") for r in recs if r.get("path")]
        # (named as the whole answer names them: _closed_pages_phase)
        label = updatepages.same_name_label(pages + list(out.get("open_at_start") or ()))
        rep = updatepages.fold_records(recs, label)
        _pages_answer(out, rep, pages, bool(out.get("stopped")), backup, partial=True, label=label)
    if backup and not out.get("backup") and os.path.isdir(backup):
        out["backup"] = backup
    return out


def _write_json(path: str, obj) -> None:
    """``obj`` as JSON into ``path``, atomically (tmp + replace); never raises."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except OSError:  # swallow-ok: an extra the editor can do without
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _closed_pages_phase(
    out: dict,
    usage,
    skip,
    builder_open,
    backup,
    progress_file: str,
    skip_file: str,
    cancel_file: str,
    stopped: list,
) -> dict:
    """Step 2 of :func:`update_closed_pages`: the closed pages, into ``out``. The pages
    step of Update Users (genieusage.update_user_pages), so both give one result."""
    from ..io import genieusage as GU
    from ..render import headless_thumb

    skip_n = {os.path.normcase(os.path.abspath(p)) for p in skip}
    want = [u.ref.path for u in usage.users if u.ref.kind == "page" and u.needs_update]
    pages = [p for p in want if os.path.normcase(os.path.abspath(p)) not in skip_n]
    # (open in an editor when the run started: updated in their tabs; one closed before the
    # run ended is named by the editor, as Update Pages names it)
    out["open_at_start"] = [
        os.path.abspath(p) for p in want if os.path.normcase(os.path.abspath(p)) in skip_n
    ]
    # (and those whose tab a save during the run cannot have made current: they use a
    # library genie saved again above, which the open tabs take only at the end)
    out["after_saves"] = GU.pages_using_saved(
        usage.users, list(out.get("elements_saved") or []) + list(out.get("elements_in_editor") or [])
    )
    if not pages:
        return out
    from ..io import updatepages

    progress, skip_now = _progress_and_skip(progress_file, skip_file, cancel_file, stopped, phase="pages")
    out["pages"] = list(pages)
    if progress_file:
        _write_json(progress_file + GENIES_SUFFIX, out)
    # (two pages of one name in two projects are named with their project: also when one of
    # them is open in a tab, which the editor names when it was closed meanwhile)
    label = updatepages.same_name_label(want)
    so_far = updatepages.UpdateReport()

    def each_page(rec):
        # (each page as it is done: the records file beside the progress file holds it too,
        # which the editor folds when this helper dies (chain_partial_answer); the answer so
        # far is counted record by record and written once, when the phase ends in an error)
        so_far.add(rec, label)

    try:
        rep = GU.update_user_pages(
            pages,
            skip=list(skip),
            backup=backup,
            builder_open=builder_open,
            progress=progress,
            skip_now=skip_now,
            on_record=each_page,
            records=(progress_file + RECORDS_SUFFIX) if progress_file else None,
            save_thumbnail=headless_thumb.page_thumbnail,
        )
    except Exception:
        # (an error of a later page comes back with the pages saved before it and the backup:
        # update_closed_pages adds the error)
        _pages_answer(out, so_far, pages, bool(stopped), backup, partial=True, label=label)
        if progress_file:
            _write_json(progress_file + GENIES_SUFFIX, out)
        raise
    return _pages_answer(out, updatepages.relabelled(rep, want), pages, bool(stopped), backup, label=label)


def _editor_state(editor_file: str):
    """The editor's state of an open element: 'clean', 'dirty' or None."""
    if not editor_file:
        return None

    def state(ref):
        want = (os.path.normcase(os.path.abspath(ref.path)), ref.element.lower())
        try:
            with open(editor_file, encoding="utf-8") as fh:
                rows = [ln.rstrip("\r\n").split("\t") for ln in fh if ln.strip()]
        except OSError:  # swallow-ok: no list (the editor removed it): none is open
            return None
        found = None
        for row in rows:
            if len(row) == 3 and (os.path.normcase(os.path.abspath(row[1])), row[2].lower()) == want:
                if row[0] == "dirty":
                    return "dirty"
                found = "clean"
        return found

    return state


def _progress_and_skip(progress_file: str, skip_file: str, cancel_file: str, stopped: list, phase: str = ""):
    """update_pages' progress(i, n, name) and skip_now(path) for a worker run: progress goes to
    ``progress_file`` (with ``phase`` when one is given), ``cancel_file`` existing stops,
    ``skip_file`` lists the pages an editor opened meanwhile. A lock wait of this helper asks
    ``cancel_file`` between its slices too (format/projectlock.set_cancel: LockCancelled)."""
    if cancel_file:
        from ..format import projectlock

        projectlock.set_cancel(lambda: os.path.exists(cancel_file))

    def progress(i, n, name):
        if cancel_file and os.path.exists(cancel_file):
            stopped.append(True)
            return False
        if progress_file:
            tmp = progress_file + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(dict({"i": i, "n": n, "name": name}, **({"phase": phase} if phase else {})), fh)
                os.replace(tmp, progress_file)
            except OSError:
                pass
        return True

    def skip_now(path):
        if not skip_file:
            return False
        try:
            with open(skip_file, encoding="utf-8") as fh:
                opened = {os.path.normcase(os.path.abspath(x.strip())) for x in fh if x.strip()}
        except OSError:
            return False
        return os.path.normcase(os.path.abspath(path)) in opened

    return progress, skip_now


#: the UpdateReport fields an Update Pages answer carries
REPORT_FIELDS = (
    "pages",
    "stale",
    "updated",
    "instances",
    "placeholders",
    "templates",
    "supergenies",
    "symbols",
    "composites",
    "composites_skipped",
    "failed",
    "backup",
    "unregistered",
    "open_in_builder",
    "skipped_open",
    "changed_meanwhile",
    "db_in_use",
    "rows_pending",
    "rows_written",
    "warnings",
    "links_dropped",
    "unchanged",
    "not_checked",
    "records",
    "recovered",
)


def update_pages_job(req: dict) -> dict:
    """Run Update Pages in this process."""
    from ..io import updatepages

    stopped: list = []
    cancel_file = req.get("cancel_file") or ""
    progress, skip_now = _progress_and_skip(
        req.get("progress_file") or "", req.get("skip_file") or "", cancel_file, stopped
    )
    part = req.get("part") or [0, 1]
    pages = req.get("pages")
    rep = updatepages.update_pages(
        list(req.get("dirs") or ()),
        skip=list(req.get("skip") or ()),
        progress=progress,
        templates=bool(req.get("templates", True)),
        genies=bool(req.get("genies", True)),
        backup=req.get("backup"),
        builder_open=[tuple(x) for x in req.get("builder_open") or ()],
        page_properties=req.get("page_properties", False),
        symbols=req.get("symbols"),
        skip_now=skip_now,
        only=req.get("only"),
        pages=list(pages) if pages is not None else None,
        claims=req.get("claims") or None,
        records=req.get("records_file") or None,
        defer_composites=bool(req.get("defer_composites")),
        part=int(part[0]),
        force=bool(req.get("force")),
    )
    ans = {
        "format": FORMAT,
        "stopped": bool(stopped) or any(r.get("outcome") == "not_checked" for r in rep.records),
    }
    for k in REPORT_FIELDS:
        v = getattr(rep, k, None)
        ans[k] = list(v) if isinstance(v, (list, tuple)) else v
    return ans


def merge_update_answers(
    answers: List[dict],
    pages: List[str],
    unregistered: List[str],
    claimed: set,
    backup: Optional[str],
    phase2: Optional[dict] = None,
) -> dict:
    """Merge the answers of a parallel Update Pages run."""
    from ..io import updatepages as UP

    final: dict = {}
    errors: List[str] = []
    notes: list = []
    stopped = False
    for a in list(answers) + ([phase2] if phase2 is not None else []):
        a = a or {}
        bad = bool(a.get("error")) or a.get("format") != FORMAT
        if bad:
            errors.append(str(a.get("error") or "a helper process gave no answer"))
        recs = (a.get("streamed") or []) if bad else (a.get("records") or a.get("streamed") or [])
        for r in recs:
            try:
                i = int(r["i"])
            except KeyError, TypeError, ValueError:
                continue
            prev = final.get(i)
            if prev is not None and prev.get("rows_written") and not r.get("rows_written"):
                r = dict(r, rows_written=1)  # (written by the pass before)
            final[i] = r
        stopped = stopped or bool(a.get("stopped"))
        notes += list(a.get("recovered") or ())
    recs = []
    for i, path in enumerate(pages):
        r = final.get(i)
        if r is None:
            if i not in claimed:
                r = UP.page_record(i, path, "not_checked")
            else:
                r = UP.page_record(
                    i, path, "failed", checked=1, detail="the helper process ended while it was updating it"
                )
        recs.append(r)
    projects: List[str] = []
    for r in recs:
        if r.get("checked") and r.get("project") not in projects:
            projects.append(r.get("project"))
    multi = len({str(r.get("project") or "").lower() for r in recs}) > 1

    def label(r: dict) -> str:
        return "%s (%s)" % (r.get("name"), r.get("project")) if multi else str(r.get("name"))

    rep = UP.fold_records(recs, label)
    rep.unregistered = list(unregistered)
    rep.backup = backup
    out = {"format": FORMAT}
    for k in REPORT_FIELDS:
        v = getattr(rep, k, None)
        out[k] = list(v) if isinstance(v, (list, tuple)) else v
    out.update(projects=projects, stopped=stopped, errors=errors, recovered=notes)
    return out


def users_command(request: str, answer: str) -> List[str]:
    from ..infra import workers

    return workers.command(
        "--update-closed-pages", "ctgedit.workers.genie_worker", "users_main", [request, answer]
    )


def users_main(argv=None) -> int:
    """``ctgedit --update-closed-pages <request.json> <answer.json>``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--update-closed-pages"]:
        args = args[1:]
    if len(args) != 2:
        return 2
    from ..doc import history as _history
    from ..render import headless_thumb

    _history.SAVE_THUMBNAIL = headless_thumb.page_thumbnail  # (fresh thumbnails)
    helper_waits()
    req_path, ans_path = args
    try:
        with open(req_path, encoding="utf-8") as fh:
            req = json.load(fh)
        if req.get("format") != FORMAT:
            raise ValueError("request format %r" % req.get("format"))
        if req.get("mode") == "pages":
            ans = update_pages_job(req)
        else:
            ans = update_closed_pages(
                req["library"],
                req["element"],
                req.get("skip") or (),
                req.get("builder_open") or (),
                req.get("backup"),
                req.get("progress_file") or "",
                req.get("skip_file") or "",
                req.get("cancel_file") or "",
                req.get("editor_file") or "",
                elements=bool(req.get("elements", True)),
            )
    except Exception as e:  # noqa: BLE001
        import traceback

        ans = {
            "format": FORMAT,
            "error": "%s: %s" % (type(e).__name__, e),
            "trace": traceback.format_exc()[-4000:],
        }
    tmp = ans_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ans, fh)
    os.replace(tmp, ans_path)
    return 0


def main(argv=None) -> int:
    """``ctgedit --genie-update <request.json> <answer.json>``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--genie-update"]:
        args = args[1:]
    if len(args) != 2:
        return 2
    from ..doc import history as _history
    from ..render import headless_thumb

    _history.SAVE_THUMBNAIL = headless_thumb.page_thumbnail  # (fresh thumbnails)
    helper_waits()
    req_path, ans_path = args
    try:
        with open(req_path, encoding="utf-8") as fh:
            req = json.load(fh)
        with open(req_path + ".page", "rb") as fh:
            data = fh.read()
        if req.get("format") != FORMAT:
            raise ValueError("request format %r" % req.get("format"))
        from ..format.fileio import adopt_changes

        adopt_changes(req.get("file_changes"))
        if req.get("element") and (req["element"].get("save") is not None):
            ans = compute_save(req["element"], data)
        elif req.get("element"):
            ans = compute_element(req["element"], data)
        else:
            ans = compute(
                req["page"],
                data,
                changes_from_json(req.get("changes") or []),
                req.get("bindings"),
                template=bool(req.get("template")),
                page_sha1=req.get("page_sha1"),
                previous=previous_from_json(req.get("previous")),
            )
    except Exception as e:  # noqa: BLE001 -- the editor reports it and leaves the page
        import traceback

        ans = {
            "format": FORMAT,
            "error": "%s: %s" % (type(e).__name__, e),
            "trace": traceback.format_exc()[-4000:],
        }
    tmp = ans_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ans, fh)
    os.replace(tmp, ans_path)
    return 0


def serve_main(argv=None) -> int:
    """``ctgedit --genie-serve``: answer requests from stdin one after another."""
    from ..doc import history as _history
    from ..render import headless_thumb

    _history.SAVE_THUMBNAIL = headless_thumb.page_thumbnail  # (fresh thumbnails)
    helper_waits()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req, _, ans = line.partition("\t")
        if req and ans:
            main([req, ans])
    return 0


def serve_command() -> List[str]:
    from ..infra import workers

    return workers.command("--genie-serve", "ctgedit.workers.genie_worker", "serve_main", [])


def progress_and_skip(progress_file: str, skip_file: str, cancel_file: str, stopped: list):
    """:func:`_progress_and_skip` for the other helper processes (picture_worker)."""
    return _progress_and_skip(progress_file, skip_file, cancel_file, stopped)
