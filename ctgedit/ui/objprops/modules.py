"""Load the sheet's page modules and their declared properties."""

from __future__ import annotations

import importlib
from typing import Dict, List

from . import registry as R

_LOADED: Dict[str, object] = {}
_MISSING = object()


def load(name: str):
    """The page module ``name`` (None when it does not exist)."""
    mod = _LOADED.get(name)
    if mod is None:
        try:
            mod = importlib.import_module("%s.%s" % (__package__, name))
        except ModuleNotFoundError as exc:
            if exc.name != "%s.%s" % (__package__, name):
                raise  # a real import error inside the module
            mod = _MISSING
        _LOADED[name] = mod
    return None if mod is _MISSING else mod


def module_names() -> List[str]:
    out = []
    for p in list(R.PAGES) + list(R.OWN_PAGES.values()):
        if p.module not in out:
            out.append(p.module)
    return out


def all_modules() -> list:
    return [m for m in (load(n) for n in module_names()) if m is not None]


def page_builder(page: R.Page):
    mod = load(page.module)
    return None if mod is None else getattr(mod, "BUILDERS", {}).get(page.id)


def tab_builder(module: str, tab: str):
    mod = load(module)
    return None if mod is None else getattr(mod, "TAB_BUILDERS", {}).get(tab)


def all_subs() -> list:
    out = []
    for m in all_modules():
        for sub in getattr(m, "SUBS", ()) or ():
            if sub not in out:
                out.append(sub)
    return out


def sheet_keys() -> set:
    """Every bag key the object sheet can write or remove (family patterns
    as 'NAME_*'): the 'no invented keys' contract tests/test_sheet_keys.py
    checks against the engine's key tables."""
    keys = set()
    for m in all_modules():
        keys.update(f.key for f in getattr(m, "FIELDS", ()) or ())
        for sub in getattr(m, "SUBS", ()) or ():
            keys.update(f.key for f in sub.fields)
            keys.update(sub.family)
    return keys
