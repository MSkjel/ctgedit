"""View > Preview States: the page drawn as the runtime shows it for values you set by hand.

Each condition and expression of the page's animated objects is a :class:`Condition` (the
same text on several objects is one condition: setting it sets them all). Nothing is
evaluated: a condition is given TRUE or FALSE, an expression a percent of its range, an
index, or the text a display shows, and :meth:`PreviewModel.states` works out what each
object then looks like with the simulator's rules (engine Page/PageRun.cs UpdateObject,
Colours.cs, Formulas.cs). A part of an object whose conditions are not set is drawn as
the page stores it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..format import codes as _codes
from ..format import geometry as G
from ..format import symbolset as SS
from ..format.cnum import c_atoi
from ..sim import pagemodel as PM
from ..sim.state import DISABLE_HIDE, DISABLE_UNCHANGED, ObjectState
from . import find_replace as FR

#: a condition's kinds: TRUE / FALSE, a percent of its range, an index into a list of
#: colours, texts or symbols, the text a numeric or string display shows
ON, PERCENT, INDEX, TEXT = "on", "percent", "index", "text"
KINDS = (ON, PERCENT, INDEX, TEXT)

#: what each kind's value means (the panel's column tip)
KIND_LABELS = {ON: "True or false", PERCENT: "Percent of its range", INDEX: "Index", TEXT: "Text shown"}

_LETTERS = "ABCDE"
#: COL_MAXON's default, a state colour nothing is stored for (fillcolour.STATE_DEFAULTS)
_DEFAULT_ON = 0x00FF00
_STATE_DEFAULTS = (0x00FF00, 0xFFFF00, 0xFF0000, 0x00FFFF, 0xFF00FF, 0xFFFFFF, 0x0000FF, 0xBFBFBF)
#: the thresholds a record without stored ones has: (comparison, percent, colour)
_DEFAULT_THRESHOLDS = ((0, 5, 0x0000FF), (0, 15, 0x000080), (1, 85, 0x800000), (1, 95, 0xFF0000))


def condition_id(kind: str, text: str) -> str:
    """The key of a condition: its kind and its text without case or extra spaces."""
    return "%s:%s" % (kind, " ".join(str(text or "").split()).lower())


@dataclass(frozen=True)
class Use:
    """One field a condition is set in: the object, the field's key and its label."""

    index: int
    key: str
    label: str


@dataclass
class Condition:
    id: str
    kind: str
    text: str
    uses: List[Use] = field(default_factory=list)

    @property
    def objects(self) -> List[int]:
        out: List[int] = []
        for u in self.uses:
            if u.index not in out:
                out.append(u.index)
        return out


@dataclass
class _Plan:
    """What one animated object's state is made from (the condition ids of its fields)."""

    index: int
    an: int
    tc: int
    get: dict
    vis: Optional[str] = None
    dis: Optional[str] = None
    col_mode: int = 0
    col: List[Optional[str]] = field(default_factory=list)
    colours: Optional[dict] = None
    fil: Optional[str] = None
    move: Tuple[Optional[str], Optional[str]] = (None, None)
    rot: Optional[str] = None
    size: Tuple[Optional[str], Optional[str]] = (None, None)
    txt_mode: int = 0
    txt: List[Optional[str]] = field(default_factory=list)
    text: Optional[dict] = None
    set_states: int = 0
    set_declared: int = 256
    rect: Optional[Tuple[int, int, int, int]] = None
    magnitude: Optional[Sequence[float]] = None


# --------------------------------------------------------------------------- the rules
def _map(p: float, lo: float, hi: float) -> float:
    """Formulas.Map: lo at 0 %, hi at 100 %, clamped."""
    if p != p:
        p = 0.0
    if p > 100.0:
        return hi
    if p < 0.0:
        return lo
    return p * (hi - lo) / 100.0 + lo


def _short(x: float) -> int:
    """C's (int)(short)(int)x."""
    t = int(x)
    t &= 0xFFFF
    return t - 0x10000 if t & 0x8000 else t


def _cshort(n: int) -> int:
    n &= 0xFFFF
    return n - 0x10000 if n & 0x8000 else n


def _pivot(mode: int, org: int, n: int) -> int:
    """Formulas.Pivot: an Express point (mode 0: -1 = 0, 1 = n, else n / 2) or a Custom offset
    from the middle."""
    half = int(_cshort(n) / 2)
    if mode == 0:
        return 0 if org == -1 else n if org == 1 else half
    return half + org


def _threshold_order(rows: List[Tuple[int, int, int]]) -> List[Tuple[int, int, int]]:
    """Colours.Sort: '<' / '<=' ascending, '>' / '>=' descending, the inclusive test first."""
    e = list(rows)
    n = len(e)
    for i in range(n - 1):
        for j in range(i + 1, n):
            a, b = e[i], e[j]
            if a[0] == b[0]:
                swap = b[1] < a[1] if a[0] in (0, 2) else a[1] < b[1]
            elif a[0] == 0:
                swap = not (a[1] < b[1]) and b[0] == 2
            elif a[0] == 1:
                swap = not (b[1] < a[1]) and b[0] == 3
            elif a[0] == 2:
                swap = not (a[1] <= b[1]) and b[0] == 0
            elif a[0] == 3:
                swap = not (b[1] <= a[1]) and b[0] == 1
            else:
                swap = False
            if swap:
                e[i], e[j] = b, a
    return e


def _threshold_colour(v: float, rows) -> Optional[int]:
    for cmp, pct, colour in rows:
        hit = (
            v < pct
            if cmp == 0
            else (pct <= v and v != pct)
            if cmp == 1
            else not (pct < v)
            if cmp == 2
            else not (v < pct)
            if cmp == 3
            else False
        )
        if hit:
            return colour
    return None


_NO_HUE = 0x1000


def _to_hls(rgb: int) -> Tuple[int, int, int]:
    r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    mx, mn = max(r, g, b), min(r, g, b)
    diff = mx - mn
    if mx == mn or abs(diff) <= 2:
        return _NO_HUE, (mn + mx) >> 1, 0
    s = mx + mn
    lum = (s >> 1) & 0xFFFF
    if lum > 0x7F:
        s = 0x1FF - mx - mn
    sat = int(diff * 255.0 / s + 0.5) & 0xFF
    if r == mx:
        hue = (g - b) / diff
    elif g == mx:
        hue = (b - r) / diff + 2.0
    else:
        hue = (r - g) / diff + 4.0
    hue *= 60.0
    if hue < 0.0:
        hue += 360.0
    return _cshort(int(hue + 0.5)), lum, sat


def _from_hls(h: int, lum: int, sat: int) -> int:
    if h == _NO_HUE or sat == 0:
        v = lum & 0xFF
        return (v << 16) | (v << 8) | v
    while h > 359:
        h -= 360
    while h < 0:
        h += 360
    L, S = lum / 255.0, sat / 255.0
    m2 = L * (1.0 + S) if lum <= 127 else L + S - L * S
    m1 = 2.0 * L - m2

    def ch(x: float) -> int:
        if x > 360.0:
            x -= 360.0
        elif x < 0.0:
            x += 360.0
        if x < 60.0:
            v = (m2 - m1) * x / 60.0 + m1
        elif x < 180.0:
            v = m2
        elif x < 240.0:
            v = (m2 - m1) * (240.0 - x) / 60.0 + m1
        else:
            v = m1
        return int(v * 255.0 + 0.5) & 0xFF

    return (ch(h + 120.0) << 16) | (ch(h) << 8) | ch(h - 120.0)


def _away(v: float) -> int:
    return int(v + 0.5) if v >= 0.0 else int(v - 0.5)


def gradient(percent: float, lo: int, hi: int) -> int:
    """Colours.GradientOne: each HLS channel from ``lo`` at 0 % to ``hi`` at 100 %."""
    p = 100.0 if percent > 100.0 else 0.0 if percent < 0.0 or percent != percent else percent
    ha, la, sa = _to_hls(lo)
    hb, lb, sb = _to_hls(hi)
    if ha == _NO_HUE and hb == _NO_HUE:
        h = _NO_HUE
    else:
        if ha == _NO_HUE:
            ha = hb
        if hb == _NO_HUE:
            hb = ha
        h = _away(ha + (hb - ha) * p / 100.0)
    return _from_hls(h, _away(la + (lb - la) * p / 100.0), _away(sa + (sb - sa) * p / 100.0))


def _unset(s: Optional[str]) -> str:
    return "" if s is None or s in ("#\n", "#\r\n") else s


# --------------------------------------------------------------------------- the model
class PreviewModel:
    """The conditions of a document's animated objects and the states they make."""

    def __init__(self, doc):
        self.doc = doc
        self.conditions: Dict[str, Condition] = {}
        self._plans: List[_Plan] = []
        self._labels: Dict[int, str] = {}
        self._build()

    # -- reading the page ----------------------------------------------------------------
    def _label(self, i: int) -> str:
        if i not in self._labels:
            from .page_checks import object_label

            self._labels[i] = object_label(self.doc, i)
        return self._labels[i]

    def _cond(self, plan: _Plan, key: str, kind: str) -> Optional[str]:
        text = str(plan.get.get(key) or "").strip()
        if not text:
            return None
        cid = condition_id(kind, text)
        c = self.conditions.get(cid)
        if c is None:
            c = self.conditions[cid] = Condition(cid, kind, text)
        what = FR.FIELD_NAMES.get(key, key)
        c.uses.append(Use(plan.index, key, "%s: %s" % (self._label(plan.index), what[:1].lower() + what[1:])))
        return cid

    def _build(self) -> None:
        doc = self.doc
        recs = doc.parsed.page.objects
        objs = doc.page.objects
        seen = set()
        for i in range(min(len(objs), len(recs))):
            o = objs[i]
            an = getattr(o, "an", -1)
            po = recs[i]
            tc = po.type_code
            if not isinstance(an, int) or an <= 0 or an in seen or tc in _codes.INPUT_TCS:
                continue
            seen.add(an)
            try:
                vals = FR.own_values(doc, i)
            except Exception:  # noqa: BLE001  # swallow-ok: a record without a value bag is not animated
                continue
            get: Dict[str, str] = {}
            for k, v in vals:
                get.setdefault(str(k).upper(), v)
            p = _Plan(i, an, tc, get)
            p.vis = self._cond(p, "VISIBLE", ON)
            p.dis = self._cond(p, "DISABLED", ON)
            p.col_mode = c_atoi(get.get("COLMODE") or "0")
            if p.col_mode == 0:
                p.col = [self._cond(p, "COL_A", ON)]
            elif p.col_mode == 1:
                p.col = [self._cond(p, "COL_" + x, ON) for x in _LETTERS]
            elif p.col_mode == 2:
                p.col = [self._cond(p, "COL_ARR", INDEX)]
            elif p.col_mode in (3, 4):
                p.col = [self._cond(p, "COL_EXP", PERCENT)]
            p.fil = self._cond(p, "FIL", PERCENT)
            p.move = (
                self._cond(p, "MVX", PERCENT) or self._cond(p, "MVXSL", PERCENT),
                self._cond(p, "MVY", PERCENT) or self._cond(p, "MVYSL", PERCENT),
            )
            p.rot = self._cond(p, "ROT", PERCENT) or self._cond(p, "ROTSL", PERCENT)
            p.size = (self._cond(p, "SZX", PERCENT), self._cond(p, "SZY", PERCENT))
            p.txt_mode = c_atoi(get.get("TXTMODE") or "0")
            if tc in (PM.TEXT_TC, PM.SET_TC):
                if p.txt_mode == 0 or (tc == PM.SET_TC and p.txt_mode >= 3):
                    p.txt = [self._cond(p, "TXT_A", ON)]
                elif p.txt_mode == 1:
                    p.txt = [self._cond(p, "TXT_" + x, ON) for x in _LETTERS]
                elif p.txt_mode == 2:
                    p.txt = [self._cond(p, "SET_ARR" if tc == PM.SET_TC else "TXT_A", INDEX)]
                elif tc == PM.TEXT_TC:
                    p.txt = [self._cond(p, "TXT_STR", TEXT)]
            if not any((p.vis, p.dis, any(p.col), p.fil, any(p.move), p.rot, any(p.size), any(p.txt))):
                continue
            try:
                if any(p.col) or p.fil:
                    p.colours = PM.decode_colour(po)
                if tc == PM.TEXT_TC and any(p.txt):
                    p.text = PM.decode_text(po)
                if tc == PM.SET_TC:
                    p.set_states = sum(
                        1
                        for m in o.members
                        if 0 <= m < len(recs) and recs[m].type_code not in _codes.INPUT_TCS
                    )
                    cs = SS.class_section(po)
                    if cs is not None:
                        p.set_declared = cs["declared"]
                gh = G.parse_geom_header(po.body_raw)
                if gh is not None:
                    p.rect = tuple(gh.rect1)
                if any(p.move):
                    p.magnitude = PM.move_magnitude(po)
            except Exception:  # noqa: BLE001
                from ..infra.logsetup import note_exception

                note_exception("state_preview._build", index=i)
            self._plans.append(p)

    # -- the conditions --------------------------------------------------------------------
    def ordered(self) -> List[Condition]:
        """The conditions in page order (the first object that uses each)."""
        return sorted(self.conditions.values(), key=lambda c: (min(u.index for u in c.uses), c.text.lower()))

    # -- the states ----------------------------------------------------------------------
    def states(self, values: Dict[str, object]) -> Dict[int, ObjectState]:
        """{AN: ObjectState} of every object a set value changes (``values`` by condition id:
        a bool, a percent, an index or a text)."""
        out: Dict[int, ObjectState] = {}
        for p in self._plans:
            st = self._state(p, values)
            if st is not None and not st.is_default():
                out[p.an] = st
        return out

    def _state(self, p: _Plan, values) -> Optional[ObjectState]:
        def has(*ids) -> bool:
            return any(c is not None and c in values for c in ids)

        def on(c) -> bool:
            return bool(values.get(c)) if c is not None else False

        def num(c) -> float:
            try:
                return float(values.get(c) or 0)
            except TypeError, ValueError:
                return 0.0

        g = p.get

        def I(key: str, default: int = 0) -> int:  # noqa: E743
            v = g.get(key)
            return c_atoi(v) if v not in (None, "") else default

        st = ObjectState(p.an)
        if has(p.vis):
            st.shown = not on(p.vis)
        if has(p.dis) and on(p.dis):
            style = I("GREY_STYLE")
            st.disabled = True
            st.disable_style = style if 1 <= style <= 5 else 1
            if style == DISABLE_HIDE:
                st.shown = False
        elif has(p.dis):
            st.disable_style = DISABLE_UNCHANGED
        if not st.shown:
            return st
        self._geometry(p, st, has, num, I)
        if p.col and has(*p.col):
            st.fill_colour = self._colour(p, on, num)
        if has(p.fil):
            st.fill_level = _map(num(p.fil), I("FILMINPC") / 100.0, I("FILMAXPC", 100) / 100.0)
            st.fill_level_direction = I("FILMODE")
            fc = (p.colours or {}).get("filcol")
            st.fill_level_colour = fc[0] if fc else None
        if p.txt and has(*p.txt):
            self._text(p, st, on, num, values)
        return st

    def _geometry(self, p: _Plan, st: ObjectState, has, num, I) -> None:
        rc = p.rect
        if has(*p.size):
            sx = _map(num(p.size[0]), I("SZXMINPC") / 100.0, I("SZXMAXPC", 100) / 100.0) if p.size[0] else 1.0
            sy = _map(num(p.size[1]), I("SZYMINPC") / 100.0, I("SZYMAXPC", 100) / 100.0) if p.size[1] else 1.0
            st.scale = (sx, sy)
            if rc is not None:
                st.scale_origin = (
                    rc[0] + _pivot(I("SZXMODE"), I("SZXORG"), rc[2] - rc[0]),
                    rc[1] + _pivot(I("SZYMODE"), I("SZYORG"), rc[3] - rc[1]),
                )
        if has(*p.move):
            mag = p.magnitude or (1.0, 1.0)
            dx = _short(_map(num(p.move[0]), -I("MVXMINPT"), I("MVXMAXPT"))) if p.move[0] else 0
            dy = _short(_map(num(p.move[1]), I("MVYMINPT"), -I("MVYMAXPT"))) if p.move[1] else 0
            st.move = (dx * mag[0], dy * mag[1])
        if has(p.rot):
            st.rotate = _map(num(p.rot), -I("ROTMINPC"), I("ROTMAXPC", 360))
            if rc is not None:
                st.rotate_origin = (
                    rc[0] + _pivot(I("ROTMODE"), I("ROTXORG"), rc[2] - rc[0]),
                    rc[1] + _pivot(I("ROTMODE"), I("ROTYORG"), rc[3] - rc[1]),
                )

    def _colour(self, p: _Plan, on, num) -> Optional[int]:
        cs = p.colours or {}
        state0 = cs.get("state0")
        maxon = cs.get("maxon")
        sel = None
        if p.col_mode == 0:
            sel = (maxon[0] if maxon else _DEFAULT_ON) if on(p.col[0]) else (state0[0] if state0 else None)
        elif p.col_mode == 1:
            sta = cs.get("sta") or []
            last = max((k + 1 for k, c in enumerate(p.col) if c is not None), default=0)
            n = 1
            if len(sta) > 1:
                n = 0
                while (1 << n) < len(sta) and n < 5:
                    n += 1
                n = max(1, n)
            else:
                n = max(1, last)
            index = 0
            for k in range(min(n, 5)):
                if on(p.col[k]):
                    index |= 1 << k
            sel = sta[index][0] if index < len(sta) else _STATE_DEFAULTS[index & 7]
        elif p.col_mode == 2:
            arr = cs.get("arr")
            count = len(arr) if arr is not None else 256
            idx = min(max(int(num(p.col[0])), 0), count - 1) if count else 0
            sel = arr[idx][0] if arr is not None and idx < len(arr) else None
        elif p.col_mode == 3:
            thr = cs.get("thr") or []
            rows = [(int(f), int(pct), int(on_c)) for f, pct, on_c, _off in thr] or list(_DEFAULT_THRESHOLDS)
            sel = _threshold_colour(num(p.col[0]), _threshold_order(rows))
            if sel is None and state0:
                sel = state0[0]
        elif p.col_mode == 4:
            if state0:
                sel = gradient(num(p.col[0]), state0[0], maxon[0] if maxon else _DEFAULT_ON)
        return sel

    def _text(self, p: _Plan, st: ObjectState, on, num, values) -> None:
        if p.tc == PM.SET_TC:
            states = p.set_states
            if p.txt_mode == 0:
                index = 1 if on(p.txt[0]) else 0
                if index >= max(states, 1):
                    index = -1
            elif p.txt_mode == 1:
                index = 0
                for k, c in enumerate(p.txt):
                    if on(c):
                        index |= 1 << k
                if index >= states:
                    index = -1
            elif p.txt_mode == 2:
                v = int(num(p.txt[0]))
                index = -1 if v < 0 or v >= p.set_declared or v >= states else v
            else:
                index = 1 if on(p.txt[0]) and states > 1 else 0
            st.symbol_state = index
            return
        ts = p.text
        if p.txt_mode == 0:
            if ts is not None:
                st.text = _unset(ts.get("on") if on(p.txt[0]) else ts.get("off"))
        elif p.txt_mode == 1:
            index = 0
            for k, c in enumerate(p.txt):
                if on(c):
                    index |= 1 << k
            if ts is not None:
                lst = ts.get("states") or []
                st.text = _unset(lst[index]) if index < len(lst) else ""
        elif p.txt_mode == 2:
            if ts is not None:
                arr = ts.get("array") or []
                idx = min(max(int(num(p.txt[0])), 0), len(arr) - 1) if arr else 0
                st.text = _unset(arr[idx]) if idx < len(arr) else ""
        else:
            st.text = str(values.get(p.txt[0]) or "")
