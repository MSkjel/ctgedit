"""View > Click Map: which objects of a page take a click or a key, and where a click goes.

The runtime gives a click to the topmost object under the pointer that takes one: a touch
command, a button or a slider (an object with keyboard commands only takes the keys typed
over it). An object without input lets the click through; one that takes input stops it,
so an object that takes clicks can lie over another and keep its clicks (the simulator's
pick: engine Sim/SimPages.cs Pick, ui/simulate/canvas.ans_at). Records later in the page
lie above earlier ones, a member above its group.

An object above that can hide (a Hidden when or a Disable when, its own or a group's) covers
the one under it only some of the time; so can one the page's Cicode hides (DspShow), which
nothing here can know."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..format import codes as _codes

Box = Tuple[int, int, int, int]

#: the touch command fields, in the order the Input tab shows them
TOUCH_FIELDS = (("CMD", "Up"), ("DOWN_CMD", "Down"), ("REPEAT_CMD", "Repeat"), ("DBLCLK_CMD", "Double-click"))
SLIDER_FIELDS = (("MVXSL", "Horizontal slider"), ("MVYSL", "Vertical slider"), ("ROTSL", "Rotational slider"))


@dataclass
class Target:
    """An object that takes input: its box (page pixels), whether it takes clicks or only
    keys, its commands as (what, Cicode), and the objects above it that take its clicks."""

    index: int
    box: Box
    label: str
    clicks: bool
    keys: bool
    commands: List[Tuple[str, str]] = field(default_factory=list)
    #: the click takers above it whose boxes overlap it (topmost first)
    above: List[int] = field(default_factory=list)
    #: how much of its box the ones above that always show cover, 0 to 1
    covered: float = 0.0
    #: ... and all of them, those that can hide too
    shadowed: float = 0.0
    #: it can hide itself (a Hidden when or Disable when, its own or a group's)
    conditional: bool = False

    @property
    def blocked(self) -> bool:
        """A click never reaches it: objects above that always show cover all of it."""
        return self.clicks and self.covered >= 1.0

    @property
    def sometimes(self) -> bool:
        """A click reaches it only while an object above it is hidden."""
        return self.clicks and not self.blocked and self.shadowed >= 1.0


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def covered_fraction(box: Box, others: Sequence[Box]) -> float:
    """How much of ``box`` the union of ``others`` covers (exact, on the grid of their edges)."""
    l, t, r, b = box
    area = (r - l) * (b - t)
    if area <= 0:
        return 0.0
    clip = [(max(l, o[0]), max(t, o[1]), min(r, o[2]), min(b, o[3])) for o in others]
    clip = [c for c in clip if c[0] < c[2] and c[1] < c[3]]
    if not clip:
        return 0.0
    xs = sorted({l, r} | {c[0] for c in clip} | {c[2] for c in clip})
    ys = sorted({t, b} | {c[1] for c in clip} | {c[3] for c in clip})
    got = 0
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if any(c[0] <= cx < c[2] and c[1] <= cy < c[3] for c in clip):
                got += (x1 - x0) * (y1 - y0)
    return min(1.0, got / float(area))


def _commands(doc, index: int, props: dict) -> Tuple[List[Tuple[str, str]], bool, bool]:
    """(commands, takes clicks, takes keys) of object ``index``."""
    from .page_checks import input_commands

    ic = input_commands(doc, index)
    touch = ic.get("touch") or {}
    out: List[Tuple[str, str]] = []
    for key, what in TOUCH_FIELDS:
        v = str(touch.get(key) or "").strip()
        if v:
            out.append((what, v))
    clicks = bool(out)
    for key, what in SLIDER_FIELDS:
        v = str(props.get(key) or "").strip()
        if v:
            out.append((what, v))
            clicks = True
    keys = False
    for k in ic.get("keys") or ():
        seq = str(k.get("KEY_SEQ") or "").strip()
        cmd = str(k.get("CMD") or "").strip()
        if seq or cmd:
            out.append(("Keys %s" % seq if seq else "Keys", cmd))
            keys = True
    return out, clicks, keys


def can_hide(doc, index: int, settled=frozenset()) -> bool:
    """Whether object ``index`` or a group it is in has a Hidden when or a Disable when
    (``settled``: the condition texts, lower case, Preview States has set: those are known)."""
    objs = doc.page.objects
    j, guard = index, 0
    while j is not None and guard < 64:
        props = objs[j].raw.get("_props") or {}
        for key in ("VISIBLE", "DISABLED"):
            v = " ".join(str(props.get(key) or "").split()).lower()
            if v and v not in settled:
                return True
        j = objs[j].parent
        guard += 1
    return False


def click_targets(doc, shown: Optional[Callable[[int], bool]] = None, settled=frozenset()) -> List[Target]:
    """The page's objects that take input, in page order, each with the click takers above it.
    ``shown(index)``: whether the object shows (Preview States hides some); default all.
    ``settled``: the conditions Preview States has set (see :func:`can_hide`)."""
    recs = doc.parsed.page.objects
    objs = doc.page.objects
    from .page_checks import object_label

    found: List[Target] = []
    for i in range(min(len(objs), len(recs))):
        o = objs[i]
        tc = recs[i].type_code
        if tc in _codes.INPUT_TCS:
            continue
        has_input = tc == _codes.TC.BUTTON or any(
            0 <= m < len(recs) and recs[m].type_code in _codes.INPUT_TCS for m in o.members
        )
        props = o.raw.get("_props") or {}
        if not has_input and not any(str(props.get(k) or "").strip() for k, _w in SLIDER_FIELDS):
            continue
        if shown is not None and not shown(i):
            continue
        try:
            l, t, r, b = doc._obj_bbox(i)
        except Exception:  # noqa: BLE001  # swallow-ok: a record without a geometry header has no box
            continue
        cmds, clicks, keys = _commands(doc, i, props)
        if tc == _codes.TC.BUTTON:
            clicks = True  # (a button takes the click, with a command or without)
        if not clicks and not keys:
            continue
        tg = Target(i, (l, t, r, b), object_label(doc, i), clicks, keys, cmds)
        tg.conditional = can_hide(doc, i, settled)
        found.append(tg)
    takers = [x for x in found if x.clicks]
    for x in found:
        above = [y for y in takers if y.index > x.index and _overlap(x.box, y.box)]
        above.sort(key=lambda y: -y.index)
        x.above = [y.index for y in above]
        if x.clicks and above:
            x.shadowed = covered_fraction(x.box, [y.box for y in above])
            fixed = [y.box for y in above if not y.conditional]
            x.covered = covered_fraction(x.box, fixed) if fixed else 0.0
    return found


def target_at(targets: Sequence[Target], x: float, y: float) -> Tuple[Optional[Target], List[Target]]:
    """(the object a click at page point (x, y) goes to, the other targets under it, topmost
    first)."""
    under = [t for t in targets if t.box[0] <= x < t.box[2] and t.box[1] <= y < t.box[3]]
    under.sort(key=lambda t: -t.index)
    taker = next((t for t in under if t.clicks), None)
    return taker, [t for t in under if t is not taker]


def summary(targets: Sequence[Target]) -> str:
    """'12 objects take clicks, 3 take keys. 1 is never reached.'"""
    n_click = sum(1 for t in targets if t.clicks)
    n_keys = sum(1 for t in targets if t.keys and not t.clicks)
    blocked = sum(1 for t in targets if t.blocked)
    sometimes = sum(1 for t in targets if t.sometimes)
    if not targets:
        return "Nothing on this page takes a click or a key."
    parts = [
        "%d object%s take%s clicks" % (n_click, "" if n_click == 1 else "s", "s" if n_click == 1 else "")
    ]
    if n_keys:
        parts.append("%d only keys" % n_keys)
    text = ", ".join(parts) + "."
    if blocked:
        text += " %d %s covered by objects that take the clicks." % (blocked, "is" if blocked == 1 else "are")
    if sometimes:
        text += " %d %s reached only while an object over it hides." % (
            sometimes,
            "is" if sometimes == 1 else "are",
        )
    return text


def by_index(targets: Sequence[Target]) -> Dict[int, Target]:
    return {t.index: t for t in targets}
