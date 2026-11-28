"""Write a project's composition cache."""

from __future__ import annotations

from ..infra import backups

from ..infra.paths import path_key

import contextlib
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import composer as CP
from . import element as E
from .fileio import write_atomic
from ..infra.logsetup import note_exception as _note_exc

LOCK_FILE = "_000.cache.lock"
#: CacheLock's message (ErrorMessage_CacheLockOccupied)
MSG_LOCKED = "Cannot lock the cache store, it could be used by another user."
CACHE_FILE = "cache.ctm"
INSTANCES = "Instances"
METADATA = "metadata.xml"
#: the page background every 8.50 cache element was written with
DEFAULT_BACKGROUND = (0x00DDDDDD, 0x00DDDDDD)

_REF_RE = re.compile(rb"_CompositionCaches\\\{([0-9a-fA-F-]{36})\}\\cache\.([0-9A-Za-z_]+)", re.I)


class CacheLockError(RuntimeError):
    pass


class ComposeError(ValueError):
    """The composite genie cannot be composed (the template or a content
    item is missing or broken); the message says which."""


#: how long :func:`cache_lock` waits for another holder, in seconds (0: refuse at once)
LOCK_WAIT = [0.0]


def set_lock_wait(seconds: float) -> None:
    """This process's wait for the cache lock (the editor 10 s, the helpers 120 s)."""
    LOCK_WAIT[0] = max(0.0, float(seconds))


@contextlib.contextmanager
def cache_lock(project_dir: str, wait: Optional[float] = None):
    """Hold the composition cache lock file while writing."""
    import time
    from . import projectlock

    if projectlock.any_held(("page", "project")):
        raise RuntimeError(
            "lock order: the composition cache lock is never waited for while a page or project lock is held"
        )
    wait = LOCK_WAIT[0] if wait is None else max(0.0, float(wait))
    root = os.path.join(project_dir, CP.STORE_FOLDER)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, LOCK_FILE)
    deadline = time.monotonic() + wait
    delay, takeovers = 0.05, 0
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError, PermissionError:
            if takeovers < 20:
                try:
                    os.remove(path)  # stale: nobody has it open
                    takeovers += 1
                    continue
                except FileNotFoundError:
                    takeovers += 1
                    continue
                except OSError:
                    pass  # held open: wait for its holder
        except OSError:
            raise CacheLockError(MSG_LOCKED) from None
        if time.monotonic() >= deadline:
            raise CacheLockError(MSG_LOCKED)
        if projectlock.cancel_requested():
            raise projectlock.LockCancelled(path)
        time.sleep(max(0.0, min(delay, deadline - time.monotonic())))
        delay = min(0.25, delay * 2)
    try:
        yield path
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.remove(path)


def composite_trailer(guid: str, background=None) -> E.ElementTrailer:
    """The trailer of a cache element (see the module docstring)."""
    bg = tuple(background) if background is not None else DEFAULT_BACKGROUND
    tr = E.default_trailer(background=bg)
    tr.props.append((E.KEY_COMPOSITE_ID, "1"))
    tr.keyboards = []
    tr.extra = [uuid.UUID(guid).bytes_le]
    return tr


def background_colour(back_color: str = "") -> Tuple[int, int]:
    """The new-element background: ``back_color`` = citect.ini [CtDraw.RSC] BackColor (the
    caller reads it: io/citect_config), else 0x00dddddd."""
    v = back_color or ""
    try:
        c = int(v, 0) & 0xFFFFFFFF if v else None
    except ValueError:
        c = None
    return (c, c) if c is not None else DEFAULT_BACKGROUND


@dataclass
class ComposeResult:
    guid: str
    hash: str
    cache_dir: str
    layout: "CP.Layout"
    written: List[str] = field(default_factory=list)
    new_template_copy: bool = False  # template copy + metadata rewritten
    new_instance: bool = False  # element + options file written

    @property
    def library(self) -> str:
        return CP.cache_library(self.guid)

    @property
    def element(self) -> str:
        return self.hash

    @property
    def library_path(self) -> str:
        return os.path.join(self.cache_dir, CACHE_FILE)


def _element_exists(lib_path: str, name: str) -> bool:
    if not os.path.isfile(lib_path):
        return False
    try:
        return E.LibraryFile.open(lib_path).find(name) is not None
    except Exception:  # noqa: BLE001
        _note_exc("composer_store._element_exists", lib=lib_path, element=name)
        return False


def _cache_current(cache_dir: str, digest: str, versions: Dict[Tuple[str, str, str], str]) -> bool:
    """The folder's metadata was made from this template text and these
    content item versions (AddOrUpdateCacheInternal)."""
    if not os.path.isfile(
        os.path.join(cache_dir, CP.copied_template_name(os.path.basename(cache_dir).strip("{}")))
    ):
        return False
    md = CP.read_metadata(os.path.join(cache_dir, METADATA))
    if md is None or md.digest != digest:
        return False
    for key, v in md.versions.items():
        if versions.get(key) != v:
            return False
    return True


def _key(item: CP.ContentItemDef) -> Tuple[str, str, str]:
    return item.project.lower(), item.library.lower(), item.item.lower()


def instance_state(ctx, model: CP.TemplateModel, digest: str, layout: CP.Layout, cache_dir: str) -> str:
    """'current' when ``cache_dir`` holds an up-to-date instance of
    ``layout`` (options file current + its element present), 'stale' when it
    holds an outdated one, 'missing' when it has none."""
    ip = os.path.join(cache_dir, INSTANCES, layout.hash + ".xml")
    stored = CP.read_metadata(ip) if os.path.isfile(ip) else None
    if stored is None:
        return "missing"
    used = {_key(it): ctx.version(it) for it in layout.used_items()}
    flags = ctx.flag_sizes() if layout.has_alarm_flags() else None
    if not CP.instance_current(stored, digest, used, flags, layout.has_alarm_flags()):
        return "stale"
    if not _element_exists(os.path.join(cache_dir, CACHE_FILE), layout.hash):
        return "stale"
    return "current"


def state_of(
    page_path: str,
    template_path: str,
    stored_values: Optional[dict],
    cache_dir: str,
    inst_hash: str,
    *,
    ctx=None,
    resolver=None,
) -> str:
    """Is the placed instance what composing the template with its options gives now?"""
    from .composer_build import ComposeContext

    with open(template_path, "rb") as fh:
        data = fh.read()
    model = CP.parsed_model(data, template_path)
    ctx = ctx or ComposeContext(page_path, resolver)
    layout = ctx.compose(model, stored_values, final=False)
    if layout.hash != inst_hash:
        return "stale"
    return instance_state(ctx, model, CP.digest_of(data), layout, cache_dir)


def compose(
    page_path: str,
    template_path: str,
    inputs: Optional[dict],
    *,
    project_dir: Optional[str] = None,
    ctx=None,
    resolver=None,
    background=None,
    final: bool = True,
    force: bool = False,
    with_thumbnail=None,
) -> ComposeResult:
    """Compose a composite genie and update the project's cache."""
    from .composer_build import BuildError, ComposeContext, build_records

    try:
        with open(template_path, "rb") as fh:
            data = fh.read()
        model = CP.parsed_model(data, template_path)
    except OSError as e:
        raise ComposeError(
            "The composite genie template %s could not be read (%s)." % (template_path, e.strerror or e)
        ) from e
    except CP.CompositionError as e:
        raise ComposeError(str(e)) from e
    ctx = ctx or ComposeContext(page_path, resolver)
    try:
        layout = ctx.compose(model, inputs, final=final)
    except CP.CompositionError as e:
        raise ComposeError(str(e)) from e
    project_dir = project_dir or os.path.dirname(os.path.abspath(page_path))
    guid = model.guid
    cache_dir = CP.store_dir(project_dir, guid)
    digest = CP.digest_of(data)
    all_versions = [(it, ctx.version(it)) for it in model.library_items()]
    res = ComposeResult(guid, layout.hash, cache_dir, layout)
    if not force:
        # nothing to write: no lock (its files are replaced atomically, so this read is what
        # the lock would have shown at this moment)
        try:
            if (
                _cache_current(cache_dir, digest, {_key(it): v for it, v in all_versions})
                and instance_state(ctx, model, digest, layout, cache_dir) == "current"
            ):
                return res
        except OSError, ValueError:  # swallow-ok: checked again under the lock
            pass
    with cache_lock(project_dir):
        os.makedirs(os.path.join(cache_dir, INSTANCES), exist_ok=True)
        if not _cache_current(cache_dir, digest, {_key(it): v for it, v in all_versions}):
            p = os.path.join(cache_dir, CP.copied_template_name(guid))
            write_atomic(p, CP.template_copy(data))
            m = os.path.join(cache_dir, METADATA)
            write_atomic(m, CP.metadata_xml(guid, digest, all_versions))
            res.written += [p, m]
            res.new_template_copy = True
        if force or instance_state(ctx, model, digest, layout, cache_dir) != "current":
            log = os.environ.get("CTG_TEST_COMPOSE_LOG")
            if log:
                # (the tests' record of which process composed: inert when unset)
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write("%d\n" % os.getpid())
            try:
                recs, hot = build_records(layout, ctx)
            except BuildError as e:
                raise ComposeError(str(e)) from e
            if not recs:
                raise ComposeError(
                    "The composite genie %s has nothing to show with these options." % model.template.name
                )
            content = E.new_element_content(
                recs,
                origin=hot,
                ext="ctm",
                normal_form=False,
                resolver=ctx.resolver,
                trailer=composite_trailer(guid, background),
            )
            # the cache library's container stamp is the save's too
            stamp = E.ctdraw_timestamp()
            content = E.refresh_identity(content, stamp)
            lib = os.path.join(cache_dir, CACHE_FILE)
            if with_thumbnail is not None:
                content = with_thumbnail(page_path, lib, layout.hash, content)
            E.store_element(lib, layout.hash, content, stamp=stamp, newer=True)
            used = [(it, ctx.version(it)) for it in layout.used_items()]
            flags = ctx.flag_sizes() if layout.has_alarm_flags() else None
            ip = os.path.join(cache_dir, INSTANCES, layout.hash + ".xml")
            migrations = None
            if os.path.isfile(ip):
                from .composites import load_instance

                saved = load_instance(ip)
                migrations = {k: v for k, (_, v) in saved.migrations.items()}
            write_atomic(ip, CP.instance_xml(guid, digest, layout.values, used, flags, migrations))
            res.written += [lib, ip]
            res.new_instance = True
    return res


# ============================================================ unused instances
def links_in(data: bytes) -> Set[Tuple[str, str]]:
    """(template guid, instance hash) of every composite link in a page /
    library file's bytes (the placed roots' links and the link table)."""
    return {
        (m.group(1).decode("ascii").lower(), m.group(2).decode("ascii").lower())
        for m in _REF_RE.finditer(data)
    }


def _project_files(d: str) -> List[str]:
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for fn in names:
        if fn.lower().endswith((".ctg", ".ctm", ".ctt", ".ctl")):
            out.append(os.path.join(d, fn))
    return out


def referencing_projects(project_dir: str) -> List[str]:
    """``project_dir`` and every project of the same install folder that
    includes it (its pages may link the caches it holds)."""
    from .projects import include_search_dirs

    me = path_key(project_dir)
    out = [project_dir]
    user = os.path.dirname(os.path.abspath(project_dir))
    try:
        names = sorted(os.listdir(user))
    except OSError:
        return out
    for n in names:
        d = os.path.join(user, n)
        if path_key(d) == me or not os.path.isdir(d):
            continue
        if not os.path.isfile(os.path.join(d, "include.dbf")) and not os.path.isfile(
            os.path.join(d, "include.DBF")
        ):
            continue
        try:
            chain = include_search_dirs(d)
        except Exception:  # noqa: BLE001
            _note_exc("composer_store.referencing_projects", project=d)
            continue
        if any(path_key(c) == me for c in chain):
            out.append(d)
    return out


def referenced_instances(project_dir: str, extra: Iterable[bytes] = ()) -> Set[Tuple[str, str]]:
    """Every (guid, hash) the pages and library elements of ``project_dir``
    -- and of the projects including it -- link, plus those in ``extra``
    (the bytes of documents open with unsaved changes)."""
    refs: Set[Tuple[str, str]] = set()
    for d in referencing_projects(project_dir):
        for p in _project_files(d):
            try:
                with open(p, "rb") as fh:
                    refs |= links_in(fh.read())
            except OSError:
                continue
    for data in extra:
        refs |= links_in(data)
    return refs


@dataclass
class UnusedInstance:
    guid: str
    hash: str
    cache_dir: str
    options_file: Optional[str]  # Instances\<hash>.xml (None: element only)
    has_element: bool


def unused_instances(project_dir: str, referenced: Set[Tuple[str, str]]) -> List[UnusedInstance]:
    """The instances in the project's store no page / element links."""
    root = os.path.join(project_dir, CP.STORE_FOLDER)
    out: List[UnusedInstance] = []
    try:
        folders = sorted(os.listdir(root))
    except OSError:
        return out
    for f in folders:
        m = re.fullmatch(r"\{([0-9a-fA-F-]{36})\}", f)
        if not m:
            continue
        guid = m.group(1).lower()
        d = os.path.join(root, f)
        names: Dict[str, Tuple[Optional[str], bool]] = {}
        try:
            for fn in os.listdir(os.path.join(d, INSTANCES)):
                if fn.lower().endswith(".xml"):
                    names[fn[:-4].lower()] = (os.path.join(d, INSTANCES, fn), False)
        except OSError:
            pass
        lib = os.path.join(d, CACHE_FILE)
        if os.path.isfile(lib):
            try:
                for n in E.LibraryFile.open(lib).element_names():
                    opt, _ = names.get(n.lower(), (None, False))
                    names[n.lower()] = (opt, True)
            except Exception:  # noqa: BLE001 -- an unreadable cache: leave it
                _note_exc("composer_store.unused_instances", lib=lib)
                continue
        for h, (opt, el) in sorted(names.items()):
            if (guid, h) not in referenced:
                out.append(UnusedInstance(guid, h, d, opt, el))
    return out


def delete_instances(items: Sequence[UnusedInstance], backup: Optional[str] = None) -> List[str]:
    """Delete the instances ``items`` (their options files and their elements
    from ``cache.ctm``, which is then packed). With ``backup`` (a folder)
    every file changed is copied there first and listed in its manifest
    (Tools > Restore Backup puts it back). Returns the files changed."""
    from .projects import packed_library

    changed: List[str] = []
    by_dir: Dict[str, List[UnusedInstance]] = {}
    for it in items:
        by_dir.setdefault(it.cache_dir, []).append(it)
    for d, its in by_dir.items():
        project_dir = os.path.dirname(os.path.dirname(d))
        with cache_lock(project_dir):
            lib = os.path.join(d, CACHE_FILE)
            gone = [it.hash for it in its if it.has_element]
            if gone and os.path.isfile(lib):
                with open(lib, "rb") as fh:
                    orig = fh.read()
                lf = E.LibraryFile.open(lib)
                names = {n.lower(): n for n in lf.element_names()}
                hit = False
                for h in gone:
                    if h.lower() in names:
                        lf.delete_element(names[h.lower()])
                        hit = True
                if hit:
                    lf.ts_modified = E.ctdraw_timestamp()
                    data = lf.to_bytes()
                    packed = packed_library(data)
                    if backup:
                        backups.backup_file(backup, "pack", lib, orig, under=project_dir)
                    write_atomic(lib, packed if packed is not None else data)
                    changed.append(lib)
            for it in its:
                if it.options_file and os.path.isfile(it.options_file):
                    if backup:
                        with open(it.options_file, "rb") as fh:
                            backups.backup_file(backup, "pack", it.options_file, fh.read(), under=project_dir)
                    os.remove(it.options_file)
                    changed.append(it.options_file)
    return changed
