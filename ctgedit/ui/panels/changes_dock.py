"""Changes since the last save: the Changes panel and Next / Previous Change."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import collections
import os
import weakref
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..style import theme
from ..style.theme import set_note_role

HELP_TOPIC = "the-window#changes-panel"
KINDS = ("new", "changed", "removed", "page")
#: the mark colour (theme.FIXED) of each kind
KIND_COLOUR = {"new": "change_new", "changed": "change_edit", "removed": "change_gone", "page": "change_edit"}
KIND_TEXT = {"new": "New", "changed": "Changed", "removed": "Removed", "page": "Page"}


@dataclass
class Change:
    """One unit that differs from the saved page (or the page itself)."""

    kind: str  # new | changed | removed | page
    index: Optional[int]  # its root record now (None: removed, the page)
    box: Optional[tuple]  # (l, t, r, b): where it is now / was saved
    label: str  # 'Genie sa_pump.pump AN12 "P1"'
    an: int = 0
    summary: str = ""  # 'moved 5, 0', 'new', 'removed', 'page colour'
    records: int = 1  # the unit's records
    saved_index: Optional[int] = None  # its root record in the saved page

    @property
    def key(self) -> tuple:
        return (self.kind, self.index, self.saved_index)


@dataclass
class ChangeSet:
    changes: List[Change] = field(default_factory=list)
    #: records changed or new, records fewer than saved
    records: int = 0
    fewer: int = 0
    #: what the page is compared with: 'the save' / 'the version of 2026-10-11 14:02:31'
    base: str = "the save"
    #: how the message says it: 'changed since' (the page against its save or a version),
    #: 'differ from' (an earlier version shown read-only, against the saved page)
    verb: str = "changed since"
    error: str = ""
    #: the record indices that differ (of the new and changed units)
    changed_records: set = field(default_factory=set)

    def count(self, kind: str) -> int:
        return sum(1 for c in self.changes if c.kind == kind)

    @property
    def units(self) -> int:
        return sum(1 for c in self.changes if c.kind != "page")

    def chip_text(self) -> str:
        """The status bar chip: short, so the status message keeps its room ('5 changed',
        '3 changes'); :meth:`chip_tip` has each kind."""
        parts = self._parts()
        if len(parts) <= 1:
            return parts[0] if parts else "No changes"
        n = self.units + (1 if self.count("page") else 0)
        return "%d changes" % n

    def chip_tip(self) -> str:
        """'1 new, 1 changed, 1 removed' (the chip's tool tip)."""
        parts = self._parts()
        return ", ".join(parts) if parts else "No changes"

    def _parts(self) -> List[str]:
        parts = ["%d %s" % (self.count(k), k) for k in ("new", "changed", "removed") if self.count(k)]
        if self.count("page"):
            parts.append("page changed")
        return parts

    def message(self) -> str:
        """'5 objects changed since the save (40 records).'"""
        n = self.units
        if not n and not self.count("page"):
            return self.nothing()
        text = "%d object%s %s %s" % (n, "" if n == 1 else "s", self.verb, self.base)
        if self.records:
            text += " (%d record%s)" % (self.records, "" if self.records == 1 else "s")
        kinds = [k for k in ("new", "changed", "removed") if self.count(k)]
        if len(kinds) > 1:
            text += ": " + ", ".join("%d %s" % (self.count(k), k) for k in kinds)
        text += "."
        if self.count("page"):
            text += " The page itself changed too."
        return text

    def nothing(self) -> str:
        return (
            "Nothing differs from %s." if self.verb == "differ from" else "Nothing changed since %s."
        ) % self.base

    def ordered(self) -> List[Change]:
        """The reading order Next Change goes in: the page first, then top to bottom, left
        to right."""

        def k(c):
            if c.box is None:
                return (-1, 0, 0)
            return (0, round(c.box[1] / 8.0), c.box[0])

        return sorted(self.changes, key=k)


# --------------------------------------------------------------------------- the saved side
@dataclass
class _Saved:
    key: tuple
    records: list
    roots: List[int]
    subtree: Dict[int, List[int]]
    parsed: object
    model: object = None  # a document of the saved page (its derived objects)
    #: the saved records' payloads, and each saved unit's (made once per version of the
    #: file)
    spay: list = None
    sigs: Dict[int, tuple] = None


#: the saved pages last read, by (file, element, mtime, size): the newest few, so switching
#: between two or three edited pages does not read their saved pages again
_SAVED_MEMO: Dict[tuple, _Saved] = {}
SAVED_KEEP = 3
from ...infra import caches as _caches  # noqa: E402

_caches.register("changes.saved_page", _SAVED_MEMO, on=("refresh", "libraries", "tests"))


def _is_element(doc) -> bool:
    return hasattr(doc, "element_name") and hasattr(doc, "session")


def _source(doc, base) -> Tuple[str, str]:
    """(file, element) the page is compared with."""
    if base is not None:
        return base.file, getattr(base, "element", "") or ""
    return doc.path, (doc.element_name if _is_element(doc) else "")


def _read_saved(doc, base) -> Optional[_Saved]:
    from ...editing_base import RecordForest

    path, element = _source(doc, base)
    if not path or not os.path.isfile(path):
        return None
    st = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), element.lower(), st.st_mtime_ns, st.st_size)
    hit = _SAVED_MEMO.get(key)
    if hit is not None:
        return hit
    if element:
        from ...format.element_session import open_element_session

        _sess, parsed = open_element_session(path, element)
    else:
        from ...format.page import parse_ctg

        parsed = parse_ctg(path)
    if parsed.page is None:
        return None
    records = list(parsed.page.objects)
    forest = RecordForest(records)
    roots = forest.roots()
    sub = {r: forest.subtree(r) for r in roots}
    spay = [_payload(po) for po in records]
    sigs = {r: tuple(spay[j] for j in sub[r]) for r in roots}
    stale = [k for k in _SAVED_MEMO if k[:2] == key[:2]]  # (older versions of it)
    for k in stale:
        _SAVED_MEMO.pop(k, None)
    while len(_SAVED_MEMO) >= SAVED_KEEP:
        _SAVED_MEMO.pop(next(iter(_SAVED_MEMO)))
    hit = _SAVED_MEMO[key] = _Saved(key, records, roots, sub, parsed, spay=spay, sigs=sigs)
    return hit


def _saved_model(doc, base, saved: _Saved):
    """A document of the saved page, drawn against the page's own project (its derived
    objects name what changed); None when it cannot be made."""
    if saved.model is not None:
        return saved.model or None
    try:
        from ...editing import EditableDocument, ElementDocument

        path, element = _source(doc, base)
        if element:
            m = ElementDocument.open_element(doc.path, doc.element_name)
            if base is not None:
                from ...format.element_session import open_element_session

                _s, parsed = open_element_session(path, element)
                m.parsed = parsed
                m._rerender_page()
        else:
            with open(path, "rb") as fh:
                data = fh.read()
            m = EditableDocument.from_bytes(data, doc.path)
        saved.model = m
    except Exception:  # noqa: BLE001 -- the panel then says 'changed' without the words
        from ...infra import logsetup

        logsetup.note_exception("changes_dock.saved_model", page=getattr(doc, "path", ""))
        saved.model = False
    return saved.model or None


def release() -> None:
    """Forget the saved page read (the marks are off and the panel is hidden)."""
    _SAVED_MEMO.clear()


# --------------------------------------------------------------------------- the compare
def _payload(po) -> bytes:
    from ...format import writer

    return writer.object_payload(po)


def _box_of_record(po) -> Optional[tuple]:
    from ...format import geometry as G

    gh = G.parse_geom_header(bytes(po.body_raw or b""))
    if gh is None:
        return None
    r = G.norm_rect(gh.rect2)
    if r[2] - r[0] <= 1 and r[3] - r[1] <= 1:
        r = G.norm_rect(gh.rect1)
    return r


def _pair_key(po) -> Optional[tuple]:
    """What pairs a unit with its saved self: its kind, its AN and the library element it
    places (None: no AN)."""
    from ...format import geometry as G

    an = G.live_an(po)
    if an <= 0:
        return None
    ref = ""
    if po.subtype_size & 0x1:
        try:
            from ...format.symbols import reference_from_body

            r = reference_from_body(po.body_raw)
            ref = "%s.%s" % (r.library, r.element) if r is not None else ""
        except Exception:  # noqa: BLE001  # swallow-ok: paired by the AN alone then
            ref = ""
    return (po.type_code, an, ref.lower())


def _record_label(po) -> str:
    """A saved record's label (as Goto Object names an object), without its document."""
    from ...format import geometry as G
    from ...features.object_labels import CLASS_LABELS, LINK_LABELS

    tc = po.type_code
    cls = CLASS_LABELS.get(tc, "Object")
    if tc == 0x08 and po.subtype_size & 0x200:
        cls = "Genie"
    if po.subtype_size & 0x1:
        try:
            from ...format.symbols import reference_from_body

            ref = reference_from_body(po.body_raw)
            if ref is not None:
                cls = "%s %s.%s" % (LINK_LABELS.get(ref.ext, "Symbol"), ref.library, ref.element)
        except Exception:  # noqa: BLE001  # swallow-ok: a label only
            pass
    an = G.live_an(po)
    return "%s AN%d" % (cls, an) if an > 0 else cls


def unit_label(doc, i: int) -> str:
    """A unit's name for the Changes panel: its kind, the library element it places, its
    object name ('Genie sa_valve.v1 "V12"', 'Composite genie SA_Navigation', 'Rectangle')."""
    from ..names import kind_name, library_link_label

    try:
        obj = doc.page.objects[i]
    except AttributeError, IndexError:
        return "Object"
    text = kind_name(obj)
    ref = getattr(obj, "genie_template", "") or obj.raw.get("_ref") or obj.raw.get("_resolved_ref")
    if ref and getattr(obj, "kind", None) is not None and obj.kind.value != "symbol_set":
        pdir = os.path.dirname(os.path.abspath(doc.path)) if getattr(doc, "path", "") else ""
        try:
            kind, name = library_link_label(ref, pdir)
            text = "%s %s" % (kind, name)
        except Exception:  # noqa: BLE001  # swallow-ok: the kind alone then
            pass
    try:
        name = doc.object_name(i)
    except Exception:  # noqa: BLE001  # swallow-ok: a label only
        name = ""
    return text + (" '%s'" % name if name else "")


def changes_since_save(doc, base=None, detail: bool = False) -> ChangeSet:
    """What differs between ``doc`` and its saved file (``base``: an earlier version,
    infra.history.Version). ``detail`` adds what changed on each changed unit (it reads the
    saved page's derived objects)."""
    out = ChangeSet(base="the save" if base is None else "the version of %s" % base.when)
    if base is None and getattr(getattr(doc, "ui", None), "version_view", None) is not None:
        out.base, out.verb = "the saved page", "differ from"
    if doc is None or getattr(doc, "parsed", None) is None or doc.parsed.page is None:
        return out
    if base is None and getattr(doc, "is_new", False):
        return out
    try:
        saved = _read_saved(doc, base)
    except Exception as e:  # noqa: BLE001 -- the marks say so instead
        from ...infra import logsetup

        logsetup.note_exception("changes_dock.read_saved", page=getattr(doc, "path", ""))
        out.error = "The saved page could not be read (%s)." % e
        return out
    if saved is None:
        if base is not None:
            out.error = "That version is no longer kept."
        return out
    from ...format import geometry as G

    recs = doc.parsed.page.objects
    page = doc.page
    pay = [_payload(po) for po in recs]
    spay = saved.spay
    out.fewer = max(0, len(saved.records) - len(recs))
    # units: equal when every record of the unit is the saved one
    roots = page.roots()
    sub = {r: page.subtree(r) for r in roots}
    by_sig = collections.defaultdict(collections.deque)
    for r in saved.roots:
        by_sig[saved.sigs[r]].append(r)

    def take(sig) -> bool:
        q = by_sig.get(sig)
        if q:
            q.popleft()
            return True
        return False

    pending = [r for r in roots if not take(tuple(pay[j] for j in sub[r] if j < len(pay)))]
    # a unit the last save wrote in its save form (its name keys, its texts measured again:
    # doc.saved_form) is the same as saved when that form is
    form: Dict[int, tuple] = {}
    cur_left = []
    for r in pending:
        js = sub[r]
        if not _is_element(doc) and js and js[-1] - js[0] + 1 == len(js):
            try:
                form[r] = tuple(_payload(po) for po in doc.saved_form(js[0], js[-1] + 1))
            except Exception:  # noqa: BLE001 -- compared as it is then
                from ...infra import logsetup

                logsetup.note_exception("changes_dock.saved_form", page=doc.path)
            if r in form and take(form[r]):
                continue
        cur_left.append(r)
    saved_left = sorted(r for q in by_sig.values() for r in q)
    # the records that differ: those of the units left with no twin among the saved ones left
    left = collections.Counter(spay[j] for s in saved_left for j in saved.subtree[s])
    for r in cur_left:
        f = form.get(r)
        for k, j in enumerate(sub[r]):
            p = f[k] if f is not None and k < len(f) else pay[j]
            if left.get(p, 0) > 0:
                left[p] -= 1
            else:
                out.changed_records.add(j)
    out.records = len(out.changed_records)
    # pair the rest by AN (a unit keeps its AN through a move or an edit)
    # (and the same library element: a new object may take the AN a deleted one had)
    s_by_an = collections.defaultdict(list)
    for r in saved_left:
        key = _pair_key(saved.records[r])
        if key is not None:
            s_by_an[key].append(r)
    paired: Dict[int, int] = {}
    for r in cur_left:
        key = _pair_key(recs[r])
        cand = s_by_an.get(key) if key is not None else None
        if cand:
            paired[r] = cand.pop(0)
    used = set(paired.values())
    model = _saved_model(doc, base, saved) if detail and saved_left else None
    for r in cur_left:
        box = doc.selection_extent([r], bounds=True)
        label = unit_label(doc, r)
        an = G.live_an(recs[r])
        if r in paired:
            s = paired[r]
            what = _summary(doc, r, model, s, sub[r], saved.subtree[s], pay, spay, saved) if detail else ""
            out.changes.append(Change("changed", r, box, label, an, what or "changed", len(sub[r]), s))
        else:
            out.changes.append(Change("new", r, box, label, an, "new", len(sub[r])))
    for s in saved_left:
        if s in used:
            continue
        po = saved.records[s]
        label = unit_label(model, s) if model is not None else _record_label(po)
        out.changes.append(
            Change(
                "removed", None, _box_of_record(po), label, G.live_an(po), "removed", len(saved.subtree[s]), s
            )
        )
    page_what = _page_summary(doc, saved, base)
    if page_what:
        out.changes.insert(0, Change("page", None, None, "Page", 0, page_what, 0))
    return out


#: (Style field, words) of what a style change says
_STYLE_WORDS = (
    ("line_width", "line width"),
    ("line_style", "line style"),
    ("line_colour", "line colour"),
    ("filled", "fill"),
    ("fill_colour", "fill colour"),
    ("three_d", "3D effect"),
    ("three_d_depth", "3D effect"),
    ("shadow_colour", "3D colours"),
    ("hilight_colour", "3D colours"),
    ("lolight_colour", "3D colours"),
    ("rectangle_style", "rectangle style"),
    ("corner_radius", "corner radius"),
    ("gradient_mode", "gradient"),
    ("gradient_colour", "gradient"),
    ("ellipse_style", "ellipse style"),
    ("start_angle", "angles"),
    ("end_angle", "angles"),
    ("polygon_open", "polygon style"),
    ("font", "font"),
    ("font_size", "font size"),
    ("text_colour", "text colour"),
    ("justify", "justification"),
    ("bold", "font style"),
    ("italic", "font style"),
    ("underline", "font style"),
    ("strikeout", "font style"),
    ("vertical", "text direction"),
)
#: (Animations field, words)
_ANIM_WORDS = (
    ("visibility", "Hidden when"),
    ("movement", "movement"),
    ("fill_colour", "colour animation"),
    ("fill_level", "fill level"),
    ("symbol_set", "symbol set"),
    ("display_value", "display value"),
    ("touch", "touch commands"),
    ("keyboard", "keyboard commands"),
    ("access_general", "name and tool tip"),
    ("access_disable", "disable"),
    ("trend", "trend"),
    ("cicode_expression", "Cicode"),
)


def _short(v, n: int = 24) -> str:
    s = " ".join(str(v if v is not None else "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _summary(doc, r: int, model, s: int, sub_now, sub_saved, pay, spay, saved) -> str:
    """What changed on unit ``r`` (saved unit ``s``), in a few words."""
    words: List[str] = []
    now = doc.page.objects[r]
    old = model.page.objects[s] if model is not None and s < len(model.page.objects) else None
    if old is not None:
        g, h = now.geometry, old.geometry
        dx, dy = g.x - h.x, g.y - h.y
        if (g.extent_x, g.extent_y) != (h.extent_x, h.extent_y):
            words.append("resized to %d × %d" % (g.extent_x, g.extent_y))
            if dx or dy:
                words.append("moved %d, %d" % (dx, dy))
        elif dx or dy:
            words.append("moved %d, %d" % (dx, dy))
        elif g.nodes != h.nodes:
            words.append("reshaped")
        ta, tb = g.transform, h.transform
        if (ta.a, ta.b, ta.c, ta.d) != (tb.a, tb.b, tb.c, tb.d):
            words.append("turned")
        params_now = dict(getattr(now, "genie_params", {}) or {})
        params_old = dict(getattr(old, "genie_params", {}) or {})
        changed = [k for k in params_now if params_now.get(k) != params_old.get(k)]
        changed += [k for k in params_old if k not in params_now]
        for k in changed[:2]:
            words.append(
                "%s: %s to %s"
                % (k, _short(params_old.get(k, "")) or "blank", _short(params_now.get(k, "")) or "blank")
            )
        if len(changed) > 2:
            words.append("%d more parameters" % (len(changed) - 2))
        if now.style.text != old.style.text:
            words.append("text: '%s' to '%s'" % (_short(old.style.text), _short(now.style.text)))
        for attr, w in _STYLE_WORDS:
            if getattr(now.style, attr, None) != getattr(old.style, attr, None) and w not in words:
                words.append(w)
        for attr, w in _ANIM_WORDS:
            if getattr(now.animations, attr, None) != getattr(old.animations, attr, None) and w not in words:
                words.append(w)
    else:
        # no derived objects of the saved page: the move from the records
        a, b = _box_of_record(saved.records[s]), doc.selection_extent([r], bounds=True)
        if a is not None and b is not None:
            if (a[2] - a[0], a[3] - a[1]) == (b[2] - b[0], b[3] - b[1]) and a[:2] != b[:2]:
                words.append("moved %d, %d" % (b[0] - a[0], b[1] - a[1]))
    if not words and not getattr(now, "is_genie", False):
        n = sum(
            1
            for k in range(max(len(sub_now), len(sub_saved)))
            if k >= len(sub_now) or k >= len(sub_saved) or pay[sub_now[k]] != spay[sub_saved[k]]
        )
        if len(sub_now) > 1 and n:
            words.append("%d member%s changed" % (n, "" if n == 1 else "s"))
    if len(words) > 4:
        words = words[:4] + ["and more"]
    return ", ".join(words)


def _page_summary(doc, saved: _Saved, base) -> str:
    """What changed on the page itself: its size, colour, properties, associations, and
    the Page Properties edits staged for the next save."""
    words = []
    pb, sp = doc.parsed.page, saved.parsed.page
    if (pb.page_width, pb.page_height) != (sp.page_width, sp.page_height) and not _is_element(doc):
        words.append(
            "size %d × %d to %d × %d" % (sp.page_width, sp.page_height, pb.page_width, pb.page_height)
        )
    at, sa = doc.parsed.attributes, saved.parsed.attributes
    if at is not None and sa is not None:
        if at.bg_color0 != sa.bg_color0:
            words.append("page colour")
        try:
            if at.page_properties() != sa.page_properties():
                words.append("page properties")
            if at.associations() != sa.associations():
                words.append("associations")
        except Exception:  # noqa: BLE001  # swallow-ok: a label only
            pass
    if base is None:
        try:
            if _staged_differs(doc):
                words.append("Page Properties not saved yet")
        except Exception:  # noqa: BLE001  # swallow-ok: a label only
            pass
    return ", ".join(words)


def _staged_differs(doc) -> bool:
    """Page Properties edits staged for the next save that differ from what is stored (an
    undo back to the stored values stages those values again: nothing to save then)."""
    state = getattr(doc, "staged_page_state", None)
    state = state() if state is not None else {}
    if not state:
        return False
    rec = state.get("page_record") or {}
    if rec:
        disk = doc.page_record_on_disk() or {}
        if any(str(disk.get(k, "") or "").strip() != str(v or "").strip() for k, v in rec.items()):
            return True
    keys = state.get("page_keys")
    if keys is not None:
        from ...editing_base import PAGE_KEY_LIMITS

        stored = _stored_page_keys(doc)

        def norm(rs):
            return [{k: str(r.get(k) or "").strip() for k in PAGE_KEY_LIMITS} for r in rs]

        if stored is None or norm(keys) != norm(stored):
            return True
    return False


def _stored_page_keys(doc) -> Optional[list]:
    """The page's keyboard commands as stored (pgkey.DBF), None when they cannot be read."""
    try:
        from ...io import pgregister

        rows = pgregister.page_keyboard_rows(
            os.path.dirname(os.path.abspath(doc.path)), os.path.splitext(os.path.basename(doc.path))[0]
        )
    except Exception:  # noqa: BLE001  # swallow-ok: 'not saved yet' is said then
        return None
    return [{k: v for k, v in r.items() if k.upper() not in ("PAGE", "AN")} for r in rows]


# --------------------------------------------------------------------------- the canvas marks
#: canvas -> (marks [(kind, box)], the change framed last: its box)
_MARKS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def set_marks(view, changes: Optional[ChangeSet], focus: Optional[tuple] = None) -> None:
    marks = [(c.kind, c.box) for c in changes.changes if c.box is not None] if changes else []
    old = _MARKS.get(view)
    new = (marks, focus)
    if old != new:
        if marks or focus:
            _MARKS[view] = new
        else:
            _MARKS.pop(view, None)
        view.viewport().update()


def marks_of(view):
    return _MARKS.get(view)


def _rect(b) -> QtCore.QRectF:
    return QtCore.QRectF(b[0], b[1], max(1.0, b[2] - b[0]), max(1.0, b[3] - b[1]))


def _paint_marks(view, painter, exposed) -> None:
    hit = _MARKS.get(view)
    if not hit:
        return
    marks, focus = hit
    from ..editor.canvas_look import mark_width  # (Settings > Selection outlines widens them)

    for kind, b in marks:
        r = _rect(b)
        if not exposed.intersects(r.adjusted(-6, -6, 6, 6)):
            continue
        c = theme.fixed(KIND_COLOUR.get(kind, "change_edit"))
        pen = QtGui.QPen(c, mark_width(1.5))
        pen.setCosmetic(True)
        wash = QtGui.QColor(c)
        if kind == "new":
            wash.setAlpha(46)
            painter.setBrush(wash)
        elif kind == "removed":
            pen.setStyle(QtCore.Qt.DashLine)
            wash.setAlpha(22)
            painter.setBrush(wash)
        else:
            painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(pen)
        painter.drawRect(r)
    if focus is not None:
        pen = QtGui.QPen(theme.color("accent"), mark_width(2.0))
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(_rect(focus).adjusted(-3, -3, 3, 3))


def _install_overlay() -> None:
    from ..editor import canvas_hooks

    if "changes" not in {o.name for o in canvas_hooks.overlays("foreground")}:
        canvas_hooks.register_overlay("changes", _paint_marks, "foreground", order=40)


def kind_icon(kind: str, size: int = 12) -> QtGui.QIcon:
    """The dot of a kind (its canvas mark's colour)."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    c = theme.fixed(KIND_COLOUR.get(kind, "change_edit"))
    p.setPen(QtGui.QPen(c, 1.5, QtCore.Qt.DashLine if kind == "removed" else QtCore.Qt.SolidLine))
    fill = QtGui.QColor(c)
    fill.setAlpha(255 if kind == "new" else 90 if kind != "removed" else 40)
    p.setBrush(fill)
    p.drawEllipse(QtCore.QRectF(1.5, 1.5, size - 3, size - 3))
    p.end()
    return QtGui.QIcon(pm)


# --------------------------------------------------------------------------- the panel
class ChangesDock(QtWidgets.QWidget):
    """The Changes panel: one row per unit that differs from the saved page."""

    #: a row was double-clicked (or Enter): its Change
    changeActivated = QtCore.Signal(object)
    #: 'Compare with the Save' (after File > Page History > Compare)
    baseResetRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(_sp(1))
        self.note = QtWidgets.QLabel("")
        self.note.setWordWrap(True)
        set_note_role(self.note, "text_dim")
        top.addWidget(self.note, 1)
        self.back_btn = QtWidgets.QPushButton("Compare with the Save")
        self.back_btn.setToolTip("Compare the page with its saved file again")
        self.back_btn.clicked.connect(self.baseResetRequested.emit)
        self.back_btn.hide()
        top.addWidget(self.back_btn)
        lay.addLayout(top)
        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setAccessibleName("Changes")
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Change", "Object", "AN", "What changed"])
        from ..tables import enable_row_copy, make_resizable

        make_resizable(self.tree, "ChangesDock/changes", stretch=3)
        enable_row_copy(self.tree)
        self.tree.itemActivated.connect(self._activated)
        lay.addWidget(self.tree, 1)
        foot = QtWidgets.QHBoxLayout()
        foot.setSpacing(_sp(0))
        self.select_btn = QtWidgets.QToolButton(self)
        self.prev_btn = QtWidgets.QToolButton(self)
        self.next_btn = QtWidgets.QToolButton(self)
        for b in (self.select_btn, self.prev_btn, self.next_btn):
            b.setAutoRaise(True)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            foot.addWidget(b)
        foot.addStretch(1)
        lay.addLayout(foot)
        self.changes: Optional[ChangeSet] = None
        self._key = None
        self.show_changes(None)
        try:
            from ..help import set_help

            set_help(self, HELP_TOPIC)
        except Exception:  # noqa: BLE001  # swallow-ok: the help link is an aid
            pass

    def set_actions(self, select_all, prev, nxt) -> None:
        """The buttons are View's own Select All Changed / Previous / Next Change."""
        self.select_btn.setDefaultAction(select_all)
        self.prev_btn.setDefaultAction(prev)
        self.next_btn.setDefaultAction(nxt)

    def show_changes(self, changes: Optional[ChangeSet], comparing: bool = False, current_key=None) -> None:
        key = (
            None
            if changes is None
            else (
                tuple((c.kind, c.index, c.saved_index, c.summary, c.label) for c in changes.changes),
                changes.base,
                changes.error,
                comparing,
            )
        )
        self.back_btn.setVisible(comparing)
        if key is not None and key == self._key:
            self._select_key(current_key)
            return
        self._key = key
        self.changes = changes
        self.tree.clear()
        if changes is None:
            self.note.setText("Open a page to see what changed since its save.")
            return
        if changes.error:
            self.note.setText(changes.error)
            return
        self.note.setText(changes.message())
        for c in changes.ordered():
            it = QtWidgets.QTreeWidgetItem(
                [KIND_TEXT.get(c.kind, c.kind), c.label, str(c.an) if c.an > 0 else "", c.summary]
            )
            it.setIcon(0, kind_icon(c.kind))
            it.setToolTip(3, c.summary)
            it.setToolTip(1, c.label)
            it.setData(0, QtCore.Qt.UserRole, c)
            self.tree.addTopLevelItem(it)
        self._select_key(current_key)

    def _select_key(self, key) -> None:
        if key is None:
            return
        for k in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(k)
            c = it.data(0, QtCore.Qt.UserRole)
            if c is not None and c.key == key:
                if self.tree.currentItem() is not it:
                    self.tree.setCurrentItem(it)
                self.tree.scrollToItem(it)
                return

    def _activated(self, item, _col: int = 0) -> None:
        c = item.data(0, QtCore.Qt.UserRole) if item is not None else None
        if c is not None:
            self.changeActivated.emit(c)


# --------------------------------------------------------------------------- the window side
class ChangesCommands:
    """The editor window's side (a MainWindow mixin): View > Mark Changes Since Save, Next /
    Previous Change, Select All Changed, the status bar chip and the Changes panel."""

    def _install_changes_ui(self) -> None:
        """After the docks, the actions and the status bar."""
        self.changes_dock.set_actions(self.act_select_changed, self.act_prev_change, self.act_next_change)
        self.changes_dock.changeActivated.connect(self.go_to_change)
        self.changes_dock.baseResetRequested.connect(self.compare_with_save)
        self.dock_changes.visibilityChanged.connect(self._changes_dock_shown)
        _install_overlay()
        try:
            from .. import help as _help

            _help.set_help(self.dock_changes, HELP_TOPIC)
        except Exception:  # noqa: BLE001  # swallow-ok: the help link is an aid
            pass

    def _make_changes_chip(self) -> QtWidgets.QToolButton:
        chip = QtWidgets.QToolButton(self)
        chip.setObjectName("changes_chip")
        chip.setAutoRaise(True)
        chip.setVisible(False)
        chip.setToolTip(
            "Green: new since the save. Amber: changed. Red dashed: removed. Click to go to the next change"
        )
        chip.clicked.connect(self.next_change)
        return chip

    # -- computing ---------------------------------------------------------------------
    def _changes_base(self):
        doc = self.doc
        return getattr(getattr(doc, "ui", None), "changes_base", None) if doc is not None else None

    def _marking(self) -> bool:
        a = getattr(self, "act_mark_changes", None)
        return a is not None and a.isChecked()

    def _changes_wanted(self) -> bool:
        dock = getattr(self, "dock_changes", None)
        return self._marking() or (dock is not None and not dock.isHidden())

    def _current_changes(self, detail: Optional[bool] = None) -> ChangeSet:
        dock = getattr(self, "dock_changes", None)
        if detail is None:
            detail = dock is not None and not dock.isHidden()
        base = self._changes_base()
        if base is not None and not os.path.isfile(base.file):
            # (the version compared with was removed since: a prune, Clear, Remove)
            self.doc.ui.changes_base = base = None
            self.statusBar().showMessage(
                "That version is no longer kept. The page is compared with its save again.", 8000
            )
        return changes_since_save(self.doc, base, detail=detail)

    def _focus(self) -> Optional[tuple]:
        """(box, key) of the change gone to last on the page shown, or None (the page is
        held weakly: a closed page is not kept for it)."""
        focus = self.__dict__.get("_change_focus")
        if not focus or focus[0]() is not self.doc or self.doc is None:
            return None
        return focus[1], focus[2]

    def _changes_refresh(self) -> None:
        """_refresh: the marks, the chip and the panel follow the page (while wanted)."""
        if not self._changes_wanted() or self.doc is None:
            self._changes_off()
            return
        cs = self._current_changes()
        self._show_change_set(cs)

    def _show_change_set(self, cs: ChangeSet) -> None:
        focus = self._focus()
        box = focus[0] if focus else None
        set_marks(self.view, cs if self._marking() else None, box if self._marking() else None)
        chip = getattr(self, "_changes_chip", None)
        if chip is not None:
            chip.setVisible(self._marking())
            chip.setText(cs.chip_text() + (" (version)" if self._changes_base() else ""))
            chip.setToolTip(
                "%s. Green: new since the save. Amber: changed. Red dashed: "
                "removed. Click to go to the next change" % cs.chip_tip()
            )
        dock = getattr(self, "changes_dock", None)
        if dock is not None and not self.dock_changes.isHidden():
            dock.show_changes(
                cs, comparing=self._changes_base() is not None, current_key=focus[1] if focus else None
            )

    def _changes_off(self) -> None:
        set_marks(self.view, None)
        chip = getattr(self, "_changes_chip", None)
        if chip is not None:
            chip.setVisible(False)
        if not self._changes_wanted():
            release()

    def _changes_mark(self) -> None:
        """View > Mark Changes Since Save, ticked or not (view._mark_changes)."""
        if not self._marking() or self.doc is None:
            self._change_focus = None
            self._changes_off()
            dock = getattr(self, "changes_dock", None)
            if dock is not None and not self.dock_changes.isHidden() and self.doc is not None:
                self._changes_refresh()
            return
        cs = self._current_changes()
        self._show_change_set(cs)
        self.statusBar().showMessage(cs.error or cs.message(), 6000)

    def _changes_dock_shown(self, on: bool) -> None:
        if on:
            self._changes_refresh()
        elif not self._marking():
            self._changes_off()

    def changed_records(self):
        """(changed or new record indices, how many fewer records than the saved page)."""
        cs = changes_since_save(self.doc, self._changes_base(), detail=False)
        return set(cs.changed_records), cs.fewer

    # -- going to a change -------------------------------------------------------------
    def next_change(self) -> None:
        """View > Next Change: select the next new or changed object and bring it into
        view, or show where the next removed one was."""
        self._step_change(1)

    def prev_change(self) -> None:
        """View > Previous Change."""
        self._step_change(-1)

    def _step_change(self, step: int) -> None:
        if self.doc is None:
            return
        if not self._marking():
            self.act_mark_changes.setChecked(True)
            self._changes_mark()
        cs = self._current_changes(detail=True)
        order = cs.ordered()
        if not order:
            self._change_focus = None
            self._show_change_set(cs)
            self.statusBar().showMessage(cs.nothing(), 4000)
            return
        focus = self._focus()
        keys = [c.key for c in order]
        k = keys.index(focus[1]) if focus and focus[1] in keys else -1
        k = (k + step) % len(order) if k >= 0 else (0 if step > 0 else len(order) - 1)
        self.go_to_change(order[k], cs=cs, n=(k + 1, len(order)))

    def go_to_change(self, c: Change, cs: Optional[ChangeSet] = None, n=None) -> None:
        """Select change ``c``'s object and bring it into view (a removed one: show where
        it was saved)."""
        if self.doc is None or c is None:
            return
        self._change_focus = (weakref.ref(self.doc), c.box, c.key)
        if c.index is not None and 0 <= c.index < len(self.doc.page.objects):
            self.view.set_selection([c.index])
            self._on_canvas_selection([c.index])
        if c.box is not None:
            self._bring_box_into_view(c.box)
        if cs is None:
            cs = self._current_changes()
        self._show_change_set(cs)
        where = "Change %d of %d: " % n if n else ""
        self.statusBar().showMessage("%s%s %s." % (where, c.label, c.summary), 6000)

    def _bring_box_into_view(self, b) -> None:
        """Centre ``b`` when it is not wholly in view; zoom out only when it does not fit."""
        view = self.view
        r = _rect(b)
        seen = view.mapToScene(view.viewport().rect()).boundingRect()
        if seen.contains(r):
            return
        if r.width() > seen.width() * 0.9 or r.height() > seen.height() * 0.9:
            view.frame_rect(r)
        else:
            view.centerOn(r.center())

    def select_all_changed(self) -> None:
        """View > Select All Changed: every new and changed object."""
        if self.doc is None:
            return
        cs = self._current_changes(detail=False)
        idx = [c.index for c in cs.changes if c.index is not None]
        if not idx:
            self.statusBar().showMessage(cs.nothing(), 4000)
            return
        self.view.set_selection(idx)
        self._on_canvas_selection(idx)
        self.statusBar().showMessage(
            "%d changed object%s selected." % (len(idx), "" if len(idx) == 1 else "s"), 4000
        )

    # -- comparing with an earlier version -----------------------------------------------
    def compare_with_version(self, version) -> None:
        """File > Page History > Compare: the marks and the Changes panel compare the page
        with ``version`` (an infra.history.Version) until Compare with the Save."""
        if self.doc is None:
            return
        self.doc.ui.changes_base = version
        self._change_focus = None
        if not self._marking():
            self.act_mark_changes.setChecked(True)
        self.dock_changes.show()
        self.dock_changes.raise_()
        self._changes_mark()

    def compare_with_save(self) -> None:
        if self.doc is not None:
            self.doc.ui.changes_base = None
        self._change_focus = None
        self._changes_mark() if self._marking() else self._changes_refresh()
