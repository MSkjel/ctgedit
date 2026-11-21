"""Hooks that let other modules add painting, drop handling and event filters to the canvas."""

from __future__ import annotations

import bisect
import math
import weakref
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

LAYERS = ("foreground", "background", "over_items")


@dataclass(frozen=True)
class Overlay:
    name: str
    fn: Callable
    layer: str
    order: int


@dataclass(frozen=True)
class DropHandler:
    name: str
    accept: Callable
    drop: Callable
    priority: int


_OVERLAYS: dict = {}
_DROPS: dict = {}
_FILTERS: dict = {}
_SNAPS: dict = {}
_POINTERS: dict = {}
#: the canvases made so far (a filter registered later is installed on them too)
_VIEWS: "weakref.WeakSet" = weakref.WeakSet()


def _note_exc(where: str) -> None:
    from ...infra.logsetup import note_exception

    note_exception("canvas_hooks." + where)


def register_overlay(name: str, fn: Callable, layer: str = "foreground", order: int = 0) -> None:
    """Paint ``fn(view, painter, exposed_scene_rect)`` on every canvas (see the module notes)."""
    if layer not in LAYERS:
        raise ValueError("layer must be one of %s" % (LAYERS,))
    _OVERLAYS[name] = Overlay(name, fn, layer, int(order))
    _update_all(background=layer == "background")


def unregister_overlay(name: str) -> None:
    o = _OVERLAYS.pop(name, None)
    if o is not None:
        _update_all(background=o.layer == "background")


def overlays(layer: str) -> list:
    """The overlays of ``layer`` in paint order."""
    return sorted((o for o in _OVERLAYS.values() if o.layer == layer), key=lambda o: (o.order, o.name))


class SnapTargets:
    """Boxes to snap to (scene coordinates: QRectF or anything with left() / top() / right() /
    bottom()) with their edges and centres sorted per axis, made once per change of the
    boxes; :meth:`nearest` finds the catch in O(log n)."""

    __slots__ = ("rects", "xs", "ys")

    def __init__(self, rects: Iterable = ()):
        self.rects = list(rects)
        xs: list = []
        ys: list = []
        for r in self.rects:
            left, right, top, bottom = r.left(), r.right(), r.top(), r.bottom()
            xs += (left, (left + right) / 2.0, right)
            ys += (top, (top + bottom) / 2.0, bottom)
        self.xs = sorted(xs)
        self.ys = sorted(ys)

    def __len__(self) -> int:
        return len(self.rects)

    def values(self, axis: int) -> list:
        """The sorted edges and centres on ``axis`` (0 = x, 1 = y)."""
        return self.xs if axis == 0 else self.ys

    def nearest(self, v: float, axis: int, tol: float) -> Optional[float]:
        """The edge or centre on ``axis`` nearest ``v`` within ``tol``, or None."""
        vals = self.xs if axis == 0 else self.ys
        if not vals:
            return None
        k = bisect.bisect_left(vals, v)
        best = None
        for j in (k - 1, k):
            if 0 <= j < len(vals):
                d = abs(vals[j] - v)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, vals[j])
        return None if best is None else best[1]

    def without(self, boxes: Iterable) -> "SnapTargets":
        """These targets without the boxes equal (to the whole pixel) to one of ``boxes`` (the
        start boxes of the objects being dragged or resized: they would catch themselves)."""
        skip = {box_key(b) for b in boxes}
        if not skip:
            return self
        keep = [r for r in self.rects if box_key(r) not in skip]
        return self if len(keep) == len(self.rects) else SnapTargets(keep)


def box_key(r) -> tuple:
    """A box to the whole pixel: (left, top, right, bottom), what :meth:`SnapTargets.without`
    compares."""
    return (
        math.floor(r.left() + 0.5),
        math.floor(r.top() + 0.5),
        math.floor(r.right() + 0.5),
        math.floor(r.bottom() + 0.5),
    )


def register_snap_targets(name: str, fn: Callable) -> None:
    """Snap to the boxes ``fn(view)`` returns too (see the module notes)."""
    _SNAPS[name] = fn


def snap_sets(view) -> list:
    """Every provider's :class:`SnapTargets` of ``view`` (the empty ones left out)."""
    out: list = []
    for name, fn in list(_SNAPS.items()):
        try:
            got = fn(view)
        except Exception:  # noqa: BLE001 -- one provider's error must not stop a drag
            _note_exc("snap_targets:%s" % name)
            continue
        if not got:
            continue
        out.append(got if isinstance(got, SnapTargets) else SnapTargets(got))
    return out


def snap_targets(view) -> list:
    """The extra snap boxes of ``view`` (QRectF, scene coordinates), every provider's."""
    out: list = []
    for s in snap_sets(view):
        out.extend(s.rects)
    return out


def register_pointer_mode(name: str, fn: Callable) -> None:
    """``fn(view)``: the cursor shape while that module has the canvas's pointer, or None (see
    the module notes)."""
    _POINTERS[name] = fn


def pointer_mode(view):
    """The cursor shape of the module that has ``view``'s pointer now, or None."""
    for name, fn in list(_POINTERS.items()):
        try:
            shape = fn(view)
        except Exception:  # noqa: BLE001 -- one hook's error must not stop the pointer
            _note_exc("pointer_mode:%s" % name)
            continue
        if shape is not None:
            return shape
    return None


def register_drop_handler(name: str, accept: Callable, drop: Callable, priority: int = 0) -> None:
    _DROPS[name] = DropHandler(name, accept, drop, int(priority))


def unregister_drop_handler(name: str) -> None:
    _DROPS.pop(name, None)


def drop_handlers() -> list:
    """The drop handlers, highest priority first."""
    return sorted(_DROPS.values(), key=lambda d: (-d.priority, d.name))


def _live_views() -> list:
    """The canvases still alive: a deleted one (its Qt object gone, the Python wrapper still
    held somewhere) is forgotten, never handed a filter or an update."""
    import shiboken6

    out = []
    for view in list(_VIEWS):
        if shiboken6.isValid(view):
            out.append(view)
        else:
            _VIEWS.discard(view)
    return out


def register_viewport_filter(name: str, factory: Callable) -> None:
    """Install ``factory(view)`` as an event filter on every canvas's viewport."""
    unregister_viewport_filter(name)
    _FILTERS[name] = factory
    for view in _live_views():
        _install_one(view, name, factory)


def unregister_viewport_filter(name: str) -> None:
    _FILTERS.pop(name, None)
    for view in _live_views():
        installed = view.__dict__.get("_hook_filters") or {}
        f = installed.pop(name, None)
        if f is not None:
            try:
                view.viewport().removeEventFilter(f)
            except RuntimeError:  # swallow-ok: the canvas is being destroyed
                pass


def _install_one(view, name: str, factory: Callable) -> None:
    try:
        f = factory(view)
    except Exception:  # noqa: BLE001 -- one hook's error must not break the canvas
        _note_exc("viewport_filter:%s" % name)
        return
    if f is None:
        return
    view.__dict__.setdefault("_hook_filters", {})[name] = f  # (a reference keeps it alive)
    view.viewport().installEventFilter(f)


def attach(view) -> None:
    """A new canvas: remember it and install the registered viewport filters on it (and
    forget it when it is destroyed)."""
    _VIEWS.add(view)
    view.destroyed.connect(lambda *_a, v=view: _VIEWS.discard(v))
    for name, factory in list(_FILTERS.items()):
        _install_one(view, name, factory)


def _update_all(background: bool = False) -> None:
    for view in _live_views():
        try:
            view.request_overlay_update(background=background)
        except RuntimeError:  # swallow-ok: the canvas is being destroyed
            pass


def paint_overlays(view, painter, rect, layer: str) -> None:
    """Call the overlays of ``layer`` (the canvas's drawForeground / drawBackground)."""
    for o in overlays(layer):
        painter.save()
        try:
            o.fn(view, painter, rect)
        except Exception:  # noqa: BLE001 -- one overlay's error must not stop the paint
            _note_exc("overlay:%s" % o.name)
        finally:
            painter.restore()


def drop_hint(view, event, scene_pos, unit) -> Optional[tuple]:
    """(handler, hint) of the first handler that takes this drag, or None."""
    for d in drop_handlers():
        try:
            hint = d.accept(view, event, scene_pos, unit)
        except Exception:  # noqa: BLE001
            _note_exc("drop_accept:%s" % d.name)
            continue
        if hint is not None:
            return d, str(hint)
    return None


def run_drop(view, event, scene_pos, unit) -> bool:
    """Hand the drop to the first handler that takes it; True when one handled it."""
    found = drop_hint(view, event, scene_pos, unit)
    if found is None:
        return False
    handler = found[0]
    try:
        return bool(handler.drop(view, event, scene_pos, unit))
    except Exception:  # noqa: BLE001
        _note_exc("drop:%s" % handler.name)
        return False
