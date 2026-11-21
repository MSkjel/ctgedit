"""Every process cache is registered with infra/caches.py."""

import importlib
import os
import pkgutil

import ctgedit

#: modules that cannot be imported on their own here (an entry point)
SKIP = {"ctgedit.__main__"}


def _modules():
    out = []
    for info in pkgutil.walk_packages(ctgedit.__path__, "ctgedit."):
        if info.name in SKIP:
            continue
        try:
            out.append(importlib.import_module(info.name))
        except Exception:  # noqa: BLE001 -- (a module that needs Windows / an install)
            continue
    return out


def test_every_process_cache_is_registered():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    mods = _modules()
    from ctgedit.infra import caches

    targets = {id(t) for _on, t in caches.registered().values()}
    missing = []
    for m in mods:
        for name, v in vars(m).items():
            if name.endswith(("_CACHE", "_MEMO")) and isinstance(v, (dict, list)):
                if id(v) not in targets:
                    missing.append("%s.%s" % (m.__name__, name))
            elif (
                callable(v)
                and hasattr(v, "cache_clear")
                and hasattr(v, "cache_info")
                and getattr(v, "__module__", None) == m.__name__
            ):
                if id(v) not in targets:
                    missing.append("%s.%s (lru_cache)" % (m.__name__, name))
    assert not missing, "not in infra/caches.py:\n" + "\n".join(sorted(missing))


def test_events_clear_what_they_name():
    from ctgedit.infra import caches

    box = {"a": 1}
    caches.register("test.box", box, on=("refresh",))
    try:
        assert "test.box" not in caches.clear("libraries") and box == {"a": 1}
        assert "test.box" in caches.clear("refresh") and box == {}
    finally:
        caches._REGISTRY.pop("test.box", None)


#: module containers filled at run time that are STATE, not a cache (emptying one would lose
#: what it says): the test below requires every other one to be registered
STATE = {
    "ctgedit.backend.graphics_builder._PARKED": "COM proxies kept alive until exit",
    "ctgedit.format.geometry._LAZY_MODULES": "modules imported on first use",
    "ctgedit.help._GENERATED": "the generated help topics the editor registers",
    "ctgedit.infra.logsetup._DETAIL_SOURCES": "the providers of the diagnostics detail",
    "ctgedit.infra.paths._OWN_START": "this process's start, read once",
    "ctgedit.infra.workers._RUNNING": "the helper processes running",
    "ctgedit.infra.workers._BUSY": "the helpers working now",
    "ctgedit.ui.window.library._USERS_WARMING": "the warm-up threads of what uses a library",
    "ctgedit.io.tagscope._CHOSEN": "the user's From: choice per page",
    "ctgedit.format.dbfcols._COL_BUILDING": "column tables being built (their events)",
    "ctgedit.sim.client._STOPPING": "engine processes being stopped",
    "ctgedit.ui.simulate.viewer._SERVICES": "the open viewers' services",
    "ctgedit.ui.simulate.window._WINDOWS": "the open Simulate windows",
    "ctgedit.ui.browser.dialog._NOTIFIER": "the page browser's change notifier",
    "ctgedit.ui.style.theme._ENGINES": "the theme's style engines",
    "ctgedit.ui.objprops.modules._LOADED": "the object sheet's page modules, loaded once",
    "ctgedit.ui.objprops.expression._EXTRA_ENTRIES": "menu entries other modules add",
    "ctgedit.editing._UNDO_DOCS": "the open documents sharing the undo budget",
    "ctgedit.infra.caches._REGISTRY": "the registry itself",
    "ctgedit.ui.objprops.validators._HOOKS": "validators other modules add",
    "ctgedit.ui.window.context_menu._SECTIONS": "the canvas menu's sections other modules add",
    "ctgedit.ui.simulate.ctsim_source._LIVE": "the live simulator sessions",
    "ctgedit.ui.style.theme._TRACKED": "the widgets the theme restyles",
    "ctgedit.ui.number_field._PENDING": "the number fields whose arrow steps wait for the pause",
    "ctgedit.ui.number_field._KEEPER": "the arrow-key filter every number field shares",
    "ctgedit.ui.editor.canvas_hooks._OVERLAYS": "the canvas overlays other modules add",
    "ctgedit.ui.editor.canvas_hooks._DROPS": "the canvas drop handlers other modules add",
    "ctgedit.ui.editor.canvas_hooks._FILTERS": "the canvas viewport filters other modules add",
    "ctgedit.ui.editor.canvas_hooks._VIEWS": "the canvases made (a filter added later goes on each)",
    "ctgedit.ui.editor.canvas_hooks._SNAPS": "the extra snap-target providers other modules add",
    "ctgedit.ui.editor.canvas_hooks._POINTERS": "the modules that can take the canvas's pointer",
    "ctgedit.ui.browser.preview._RENDERERS": "the render-process runners by name (their running processes)",
    "ctgedit.ui.editor.underlay._QUEUE": "the queue of page underlays waiting for the render process",
    "ctgedit.ui.panels.changes_dock._MARKS": "each canvas's Mark Changes marks (weak keys: the view)",
    "ctgedit.ui.panels.history_dock._FLASH": "each canvas's Undo flash, for 400 ms (weak keys)",
    "ctgedit.ui.style.glyphs_nav._ICONS": "the navigation icons, one per name (engines kept alive)",
    "ctgedit.ui.style.glyphs_nav._CLONES": "the icon engines Qt cloned (kept alive)",
    "ctgedit.ui.panels.status_items._ITEMS": "the status bar fields modules register",
    "ctgedit.ui.panels.status_items._WINDOWS": "the windows whose status bar is built (a field "
    "registered later joins them)",
    "ctgedit.ui.jobs.tasks._REGISTRY": "the background tasks registry, one per process",
    "ctgedit.format.projectlock._HANDLES": "the lock mutex handles, opened once per name",
    "ctgedit.format.projectlock._RLOCKS": "the per-process half of each lock (not Windows)",
    "ctgedit.format.projectlock._FDS": "the lock files held now (not Windows)",
    "ctgedit.format.projectlock._SA": "the locks' security attributes, built once and kept "
    "alive for the life of the process",
    "ctgedit.format.journal._NOTES": "the journals rolled back, until a report takes them",
    "ctgedit.format.ctf._ZLIB_LOADED": "the install's zlib.dll, loaded once per path",
}

_EMPTY_CALLS = (
    "dict",
    "list",
    "set",
    "OrderedDict",
    "defaultdict",
    "WeakValueDictionary",
    "WeakKeyDictionary",
    "WeakSet",
    "deque",
)


def _runtime_filled(path):
    """The module-level private CONSTANT-named containers created empty in ``path``."""
    import ast
    import re

    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    out = []
    for node in tree.body:
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        empty = (
            isinstance(value, (ast.Dict, ast.List, ast.Set))
            and not getattr(value, "keys", getattr(value, "elts", None))
        ) or (
            isinstance(value, ast.Call)
            and not value.keywords
            and (getattr(value.func, "id", None) or getattr(value.func, "attr", None)) in _EMPTY_CALLS
            and (
                not value.args or getattr(value.func, "id", getattr(value.func, "attr", "")) == "defaultdict"
            )
        )
        if not empty:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and re.fullmatch(r"_[A-Z][A-Z0-9_]*", t.id):
                out.append(t.id)
    return out


def test_every_runtime_filled_container_is_registered_or_named_state():
    """A module container filled at run time is a cache (registered: its events say when
    it is emptied) or state (STATE, with why)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ctgedit.infra import caches

    mods = {m.__name__: m for m in _modules()}
    targets = {id(t) for _on, t in caches.registered().values()}
    root = os.path.dirname(ctgedit.__file__)
    missing = []
    for name, m in mods.items():
        path = getattr(m, "__file__", "") or ""
        if not path.endswith(".py") or not path.startswith(root):
            continue
        for var in _runtime_filled(path):
            full = "%s.%s" % (name, var)
            v = getattr(m, var, None)
            if full in STATE or v is None or id(v) in targets:
                continue
            missing.append(full)
    assert not missing, "register these (infra/caches.py) or name them in STATE:\n" + "\n".join(
        sorted(missing)
    )
