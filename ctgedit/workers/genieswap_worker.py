"""Replace Genie across projects, in a helper process."""

from __future__ import annotations

import contextlib
import os
from typing import List, Set, Tuple

from . import batchjob

#: the request / answer format (both sides check it)
FORMAT = "ctgedit-replace-genie/3"
#: the backup's kind (infra/backups; Tools > Restore Backup lists it)
BACKUP_KIND = "replace-genie"
#: the library files whose elements can place a genie
LIB_EXTS = (".ctm", ".ctt")


def old_genies(req: dict) -> Set[Tuple[str, str]]:
    """The genies to replace, lower case (library, element)."""
    return {(str(lib).lower(), str(el).lower()) for lib, el in req.get("old") or ()}


def _matches(doc, olds) -> List[int]:
    """The roots of ``doc``'s replaceable instances of one of ``olds``."""
    return [i for i, link in doc.replaceable_genies() if (link[0].lower(), link[1].lower()) in olds]


def _key(path: str, element: str = "") -> Tuple[str, str]:
    return os.path.normcase(os.path.abspath(path)), element.lower()


def _label(path: str, element: str) -> str:
    return "%s.%s" % (os.path.splitext(os.path.basename(path))[0], element)


def _project_libraries(project_dirs) -> List[str]:
    """The genie and template library files of the projects (their own folders)."""
    out = []
    for d in project_dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:  # swallow-ok: a project folder that cannot be listed has no library
            continue
        out += [os.path.join(d, f) for f in names if f.lower().endswith(LIB_EXTS)]
    return out


def _names_one_of(names, olds) -> bool:
    from ..io.genieusage import entry_key

    for n in names:
        k = entry_key(n)
        if k is not None and k[2] == "ctm" and k[:2] in olds:
            return True
    return False


def places(page_path: str, olds) -> bool:
    """The page's link table names one of the genies ``olds`` ({(library, element)}, lower
    case)."""
    from ..io.catalog import catalog_for

    return _names_one_of(catalog_for().link_names(page_path), olds)


def placing_elements(lib_path: str, olds) -> List[str]:
    """The elements of a library file whose link table names one of the genies ``olds``."""
    from ..io import genieusage as GU

    return [t.name for t in GU.library_tables(lib_path).values() if _names_one_of(t.names, olds)]


def _shipped(path: str) -> bool:
    from ..format.template_place import shipped_project

    return shipped_project(path) is not None


def find(req: dict, progress=None) -> dict:
    """Find the pages and elements that use the old genies."""
    from ..editing import EditableDocument, ElementDocument

    olds = old_genies(req)
    out = {
        "format": FORMAT,
        "pages": [],
        "elements": [],
        "failed": [],
        "params": [],
        "nested_pages": [],
        "nested_elements": [],
        "shipped": [],
        "follow_pages": [],
        "follow_elements": [],
    }
    seen_params = set()

    def look(doc, label: str, nested: str):
        """The roots to replace in ``doc`` (its parameters noted), or None (named in
        ``out[nested]`` when it has none)."""
        roots = _matches(doc, olds)
        if not roots:
            out[nested].append(label)
            return None
        for i in roots:
            for k in doc.genie_instance_bindings(i):
                if k.lower() not in seen_params:
                    seen_params.add(k.lower())
                    out["params"].append(k)
        return roots

    projects = list(req.get("projects") or ())
    pages = batchjob.project_pages(projects)
    libs = _project_libraries(projects)
    n_all = len(pages) + len(libs)
    for n, path in enumerate(pages):
        name = batchjob.page_name(path)
        if progress is not None and progress(n, n_all, name) is False:
            out["stopped"] = True
            return out
        proj = os.path.basename(os.path.dirname(path))
        label = "%s\\%s" % (proj, name)
        try:
            if not places(path, olds):
                continue
            if _shipped(path):
                out["shipped"].append(label)
                continue
            roots = look(EditableDocument.open(path), label, "nested_pages")
            if roots:
                out["pages"].append({"page": path, "name": name, "count": len(roots), "project": proj})
        except Exception as e:  # noqa: BLE001 -- swallow-ok: the page is reported, the run goes on
            out["failed"].append("%s: %s" % (name, e))
    for n, lib in enumerate(libs, len(pages)):
        if progress is not None and progress(n, n_all, os.path.basename(lib)) is False:
            out["stopped"] = True
            return out
        try:
            names = placing_elements(lib, olds)
        except Exception as e:  # noqa: BLE001 -- swallow-ok: the library is reported, the run goes on
            out["failed"].append("%s: %s" % (os.path.basename(lib), e))
            continue
        for el in names:
            label = _label(lib, el)
            if _shipped(lib):
                out["shipped"].append(label)
                continue
            try:
                doc = ElementDocument.open_element(lib, el)
                roots = look(doc, label, "nested_elements")
                if roots:
                    out["elements"].append(
                        {
                            "library": lib,
                            "element": el,
                            "label": label,
                            "count": len(roots),
                            "kind": doc.kind,
                            "project": os.path.basename(os.path.dirname(lib)),
                        }
                    )
            except Exception as e:  # noqa: BLE001 -- swallow-ok: the element is reported
                out["failed"].append("%s: %s" % (label, e))
    if out["elements"]:
        _follow(out)
    out["checked"] = len(pages)
    out["checked_libraries"] = len(libs)
    return out


def _follow(out: dict) -> None:
    """``out``'s 'follow_pages' / 'follow_elements': the users of its library genies (the
    usage index of their projects and the projects that include them), less what is
    changed directly."""
    from ..io import genieusage as GU

    try:
        projects, seen = [], set()
        for e in out["elements"]:
            for p in GU.scope_projects(e["library"]):
                if p.key not in seen:
                    seen.add(p.key)
                    projects.append(p)
        idx = GU.build_index(projects)
    except Exception as e:  # noqa: BLE001 -- swallow-ok: told in the answer
        out["failed"].append("what uses the genies could not be read: %s" % e)
        return
    direct = {_key(p["page"]) for p in out["pages"]}
    changed = {_key(e["library"], e["element"]) for e in out["elements"]}
    pages, elements = {}, {}
    for e in out["elements"]:
        for u in idx.usage(e["library"], e["element"]).users:
            if u.ref.kind == "page":
                k = _key(u.ref.path)
                if k not in direct:
                    pages[k] = "%s\\%s" % (
                        u.ref.project or os.path.basename(os.path.dirname(u.ref.path)),
                        batchjob.page_name(u.ref.path),
                    )
            else:
                k = _key(u.ref.path, u.ref.element)
                if k not in changed:
                    elements[k] = u.ref.label
    out["follow_pages"] = sorted(pages.values())
    out["follow_elements"] = sorted(elements.values())


def _order_elements(elements: List[dict]) -> List[dict]:
    """The library genies in the order to save them: one that places another of them
    after it (its save then composes that one's new definition)."""
    from ..format.symbols import SymbolResolver
    from ..io import genieusage as GU

    norm = os.path.normcase
    # (keyed by the library FILE: two projects' same-named library elements are two
    # elements, each entry resolved on the search path of the library that holds it)
    keys = {(norm(os.path.abspath(e["library"])), e["element"].lower()): e for e in elements}
    stems = {
        (os.path.splitext(os.path.basename(e["library"]))[0].lower(), e["element"].lower()) for e in elements
    }
    extra = [d for d in os.environ.get("CTG_LIB_PATH", "").split(os.pathsep) if d]
    deps = {}
    for e in elements:
        try:
            t = GU.library_tables(e["library"]).get(e["element"].lower())
            names = t.names if t is not None else ()
        except Exception:  # noqa: BLE001 -- swallow-ok: no known dependency then
            names = ()
        mine = set()
        res = None
        for nm in names:
            k = GU.entry_key(nm)
            if k is None or k[:2] not in stems:
                continue
            try:
                res = res or SymbolResolver.for_page(e["library"], extra)
                f = next(
                    (f for f in res._find_library_files(k[0], k[2]) if k[1] in GU.library_tables(f)), None
                )
            except Exception:  # noqa: BLE001 -- swallow-ok: no known dependency then
                f = None
            other = keys.get((norm(os.path.abspath(f)), k[1])) if f else None
            if other is not None and other is not e:
                mine.add(id(other))
        deps[id(e)] = mine
    out, done = [], set()
    while len(out) < len(elements):
        ready = [e for e in elements if id(e) not in done and deps[id(e)] <= done]
        if not ready:  # (a cycle: the order given)
            ready = [next(e for e in elements if id(e) not in done)]
        for e in ready:
            done.add(id(e))
            out.append(e)
    return out


def apply(req: dict, progress=None, skip_now=None) -> dict:
    """Replace the genies on pages and elements."""
    from ..editing import EditableDocument, ElementChangedMeanwhile, ElementDocument, libraries_changed
    from ..format.projectlock import LockCancelled
    from ..render import headless_thumb

    olds = old_genies(req)
    new = tuple(req["new"])
    mapping = dict(req.get("mapping") or {})
    old_params = list(req.get("old_params") or ())
    keep = req.get("keep") or "origin"
    backup = req.get("backup")
    out = batchjob.batch_answer(
        FORMAT,
        backup,
        changed=0,
        elements_updated=[],
        elements_open=[],
        elements_skipped=[],
        chain_elements_saved=[],
        chain_pages_updated=[],
        chain_failed=[],
        chain_skipped=[],
        chain_open=[],
        chain_unregistered=[],
    )
    skip_e = {_key(p, e) for p, e in req.get("skip_elements") or ()}

    def replace_in(doc):
        roots = _matches(doc, olds)
        if not roots:
            return 0
        return doc.replace_genies(roots, new[0], new[1], mapping, keep=keep, old_params=old_params)

    elements = _order_elements([dict(e) for e in req.get("elements") or ()])
    saved: List[dict] = []
    # -- 1. the library genies
    for n, e in enumerate(elements):
        lib, name = e["library"], e["element"]
        label = _label(lib, name)
        if progress is not None and progress(n, len(elements), label) is False:
            out["stopped"] = True
            return out
        if _key(lib, name) in skip_e:
            out["elements_open"].append(label)
            continue
        if _shipped(lib):
            out["elements_skipped"].append("%s: it is in a library supplied with Plant SCADA" % label)
            continue
        try:
            doc = ElementDocument.open_element(lib, name)
            if getattr(doc, "read_only", False):
                out["elements_skipped"].append("%s: its library file is read only" % label)
                continue
            done = replace_in(doc)
            if not done:
                continue
            if backup:
                from ..io.genieusage import backup_library

                backup_library(lib, backup, BACKUP_KIND)
            doc.save(
                expect_stored=True,
                page_thumbnail=headless_thumb.page_thumbnail,
                thumbnail_for=headless_thumb.element_thumbnail,
            )
            libraries_changed()
            out["elements_updated"].append(label)
            out["changed"] += done
            saved.append(e)
        except ElementChangedMeanwhile:
            out["failed"].append("%s: saved somewhere else while being changed. Run it again." % label)
        except LockCancelled:
            out["stopped"] = True
            return out
        except Exception as ex:  # noqa: BLE001 -- swallow-ok: the element is reported, the run goes on
            out["failed"].append("%s: %s" % (label, ex))
    # -- 2. the pages (a page of a project supplied with Plant SCADA is never written)
    pages = []
    for p in req.get("pages") or ():
        if _shipped(p):
            out["failed"].append("%s: it is in a project supplied with Plant SCADA" % batchjob.page_name(p))
        else:
            pages.append(p)

    def edit(path):
        doc = EditableDocument.open(path)
        return doc, replace_in(doc)

    for _name, done in batchjob.edit_pages(
        pages, edit, out, req=req, kind=BACKUP_KIND, progress=progress, skip_now=skip_now
    ):
        out["changed"] += done
    if out.get("stopped"):
        return out
    # -- 3. what uses the library genies saved
    if saved and req.get("follow", True):
        _chain(req, saved, out)
    return out


def _chain(req: dict, saved: List[dict], out: dict) -> None:
    """genie_worker.update_closed_pages for each library genie saved (the users the earlier
    ones brought up to date are current for the later ones: nothing is written twice)."""
    from . import genie_worker as GW

    pfile = req.get("progress_file") or ""
    skip = sorted(os.path.normcase(os.path.abspath(p)) for p in req.get("skip") or ())
    for e in saved:
        try:
            a = GW.update_closed_pages(
                e["library"],
                e["element"],
                skip=skip,
                builder_open=req.get("builder_open") or (),
                backup=req.get("backup"),
                progress_file=pfile,
                skip_file=req.get("skip_file") or "",
                cancel_file=req.get("cancel_file") or "",
                editor_file=req.get("editor_file") or "",
            )
        except Exception as ex:  # noqa: BLE001 -- swallow-ok: told in the answer
            out["chain_failed"].append("%s: %s" % (_label(e["library"], e["element"]), ex))
            continue
        finally:
            for suffix in (GW.GENIES_SUFFIX, GW.RECORDS_SUFFIX):
                with contextlib.suppress(OSError):
                    if pfile:
                        os.remove(pfile + suffix)
        for k, dst in (
            ("elements_saved", "chain_elements_saved"),
            ("elements_in_editor", "chain_elements_saved"),
            ("updated", "chain_pages_updated"),
            ("elements_skipped", "chain_skipped"),
            ("elements_failed", "chain_failed"),
            ("failed", "chain_failed"),
            ("skipped_open", "chain_open"),
            # (a page its project does not list is left, as Update Pages
            # leaves it)
            ("unregistered", "chain_unregistered"),
            ("open_in_builder", "open_in_builder"),
        ):
            for v in a.get(k) or ():
                if v not in out[dst]:
                    out[dst].append(v)
        # (what the editor's end of the chain needs: the tabs it saved from their clean
        # state, the libraries and pages it wrote; LibraryCommands._after_chain_saves)
        ca = out.setdefault("chain_answer", {})
        for k in ("tabs_saved", "libraries", "page_versions", "elements_saved", "elements_in_editor"):
            have = ca.setdefault(k, [])
            for v in a.get(k) or ():
                if v not in have:
                    have.append(v)
        if a.get("save_warning") and not ca.get("save_warning"):
            ca["save_warning"] = a["save_warning"]
        if a.get("error"):
            out["chain_failed"].append(str(a["error"]))
        if a.get("stopped"):
            out["stopped"] = True
            return


def command(request: str, answer: str) -> List[str]:
    from ..infra import workers

    return workers.command("--replace-genie", "ctgedit.workers.genieswap_worker", "main", [request, answer])


def main(argv=None) -> int:
    """``ctgedit --replace-genie <request.json> <answer.json>``: a find or an apply."""

    def dispatch(req, progress, skip_now):
        if req.get("mode") == "find":
            return find(req, progress)
        return apply(req, progress, skip_now)

    return batchjob.helper_main(argv, "--replace-genie", FORMAT, dispatch)
