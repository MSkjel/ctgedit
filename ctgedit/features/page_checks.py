"""Tools > Check Page: problems on a page before it is compiled or run."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import find_replace as FR
from ..model.document import ObjectKind

ERROR, WARNING, INFO = "error", "warning", "info"

#: the fields that hold a command (ACTION holds a key action word such as KEY_LBUTTON)
COMMAND_KEYS = frozenset(("CMD", "DOWN_CMD", "REPEAT_CMD", "DBLCLK_CMD"))
#: the fields that hold an expression (the simulator's and the Properties panel's lists;
#: the other SCOPE_EXPR keys hold colours, numbers, modes and names)
EXPRESSION_KEYS = frozenset(
    (
        "MVX",
        "MVY",
        "ROT",
        "SZX",
        "SZY",
        "MVXSL",
        "MVYSL",
        "ROTSL",
        "FIL",
        "COL_A",
        "COL_B",
        "COL_C",
        "COL_D",
        "COL_E",
        "COL_EXP",
        "COL_ARR",
        "TXT_A",
        "TXT_B",
        "TXT_C",
        "TXT_D",
        "TXT_E",
        "TXT_STR",
        "SET_ARR",
        "VISIBLE",
        "DISABLED",
        "AI_EXPR",
        "EXPR",
    )
)


@dataclass
class Finding:
    """One problem: ``level`` error | warning | info, the object (``index`` None = the
    page), the field's key, a short ``code`` and the message."""

    level: str
    message: str
    index: Optional[int] = None
    field: str = ""
    code: str = ""
    #: the object's label (kind, AN and name) when it was found
    label: str = ""
    #: the tag or page an engine warning names
    subject: str = ""
    #: its 1-based column in the field (0: not known)
    column: int = 0
    #: the object's record when it was found (stamp_records): the row finds the object
    #: again after an edit moved it in the list (resolve)
    # (dataclasses.field: 'field' here is the finding's own field above)
    record: Any = dataclasses.field(default=None, compare=False, repr=False)


def stamp_records(findings, records) -> None:
    """Give each finding about an object its record from ``records`` (the page's records as
    the check read them)."""
    for f in findings:
        if f.record is None and f.index is not None and 0 <= f.index < len(records):
            f.record = records[f.index]


def resolve(doc, f: Finding) -> Optional[int]:
    """Where the object of finding ``f`` is on the page now: its record (an object added or
    deleted above it moves it), or the record an edit put in its place. None when it is
    gone."""
    from ..format.geometry import successor_index

    objs = doc.parsed.page.objects
    if f.index is None:
        return None
    if f.record is None:
        return f.index if 0 <= f.index < len(objs) else None
    if 0 <= f.index < len(objs) and objs[f.index] is f.record:
        return f.index
    for k, o in enumerate(objs):
        if o is f.record:
            return k
    return successor_index(objs, f.record, f.index)


@dataclass
class ExpressionField:
    """An expression or command field the engine can compile."""

    index: int
    key: str
    kind: str  # 'expression' | 'command'
    text: str
    label: str


@dataclass
class Report:
    page: str
    findings: List[Finding] = field(default_factory=list)
    #: how many expression and command fields the page has (the engine's queue)
    expressions: int = 0
    #: those fields (expression_fields of the checked document)
    fields: List[ExpressionField] = field(default_factory=list)
    #: how many the engine compiled (the window counts up)
    compiled: int = 0
    #: the engine could not be used ('' = it could, or was not tried yet)
    engine_note: str = ""

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)

    def summary(self) -> str:
        return count_summary(self.count)


# --------------------------------------------------------------------------- helpers
def input_commands(doc, index: int) -> dict:
    """The object's touch and keyboard commands as the Input tab shows them."""
    try:
        return doc.input_commands(index) or {}
    except Exception:  # noqa: BLE001  # swallow-ok: an object without an input frame has none
        return {}


def object_label(doc, index: int) -> str:
    """'Button AN 12 Start' (kind, AN, name) of a record; an input record names its owner."""
    try:
        objs = doc.page.objects
        o = objs[index]
        from ..format import inputs

        if o.raw.get("_type_code") in inputs.INPUT_TCS and o.parent is not None:
            o = objs[o.parent]
        kind = o.kind.value.replace("_", " ") if isinstance(o.kind, ObjectKind) else str(o.kind)
        parts = [kind.capitalize()]
        if getattr(o, "an", 0) > 0:
            parts.append("AN %d" % o.an)
        name = (
            getattr(o.animations.access_general, "name", "") if o.animations.access_general else ""
        ) or getattr(o, "name", "")
        if name:
            parts.append(str(name))
        return " ".join(parts)
    except Exception:  # noqa: BLE001  # swallow-ok: a label only
        return "Object %d" % index


def expression_fields(doc) -> List[ExpressionField]:
    """Every expression and command field of the page's records, in record order (a genie
    member's substituted values; the template's records too: they run with the page)."""
    out: List[ExpressionField] = []
    objs = doc.parsed.page.objects
    sem = doc.page.objects
    n = len(sem)
    for i in range(min(n, len(objs))):
        if sem[i].kind is ObjectKind.UNKNOWN:
            continue  # a version 3 / 4 record: its bag is not Cicode
        try:
            fields = FR.own_fields(doc, i)
        except Exception:  # noqa: BLE001  # swallow-ok: a record without a value bag has no fields
            continue
        for scope, _kind, key, value, label in fields:
            up = (key or "").upper()
            if scope != FR.SCOPE_EXPR or not (up in EXPRESSION_KEYS or up in COMMAND_KEYS):
                continue
            text = (value or "").strip()
            if not text:
                continue
            kind = "command" if up in COMMAND_KEYS else "expression"
            out.append(ExpressionField(i, key, kind, text, label))
    return out


# --------------------------------------------------------------------------- the checks
def _check_empty_objects(doc, objs, rep: Report, label: Callable[[int], str]) -> None:
    """Buttons without a command, numbers and symbol sets without an expression."""
    for i, o in enumerate(objs):
        if o.raw.get("_from_template") or o.raw.get("_under_missing"):
            continue
        if o.kind is ObjectKind.BUTTON:
            ic = input_commands(doc, i)
            touch = ic.get("touch") or {}
            cmds = [
                k for k in ("CMD", "DOWN_CMD", "REPEAT_CMD", "DBLCLK_CMD") if str(touch.get(k) or "").strip()
            ]
            keys = [k for k in ic.get("keys") or () if str(k.get("CMD") or "").strip()]
            if not cmds and not keys:
                rep.findings.append(
                    Finding(
                        WARNING,
                        "The button has no command: nothing happens when it is "
                        "pressed. Give it an Up command on its Input tab, or make it a "
                        "text.",
                        i,
                        "CMD",
                        "no_command",
                        label(i),
                    )
                )
        elif o.kind is ObjectKind.NUMBER:
            dv = o.animations.display_value
            if dv is None or not (dv.expression or "").strip():
                # (a V3 / V4 Number keeps it in EXPR)
                key = "EXPR" if o.raw.get("_type_code") in (0x1004, 0x1005) else "TXT_A"
                rep.findings.append(
                    Finding(
                        WARNING,
                        "The number has no expression: it shows nothing at runtime.",
                        i,
                        key,
                        "no_expression",
                        label(i),
                    )
                )
        elif o.kind is ObjectKind.SYMBOL_SET:
            ss = o.animations.symbol_set
            if ss is not None and not any((e or "").strip() for e in ss.expressions):
                rep.findings.append(
                    Finding(
                        WARNING,
                        "The symbol set has no expression: it always shows its first symbol.",
                        i,
                        "TXT_A",
                        "no_expression",
                        label(i),
                    )
                )


def _check_off_page(doc, page, objs, rep: Report, label: Callable[[int], str]) -> None:
    """Top-level objects outside the page."""
    app = getattr(page, "appearance", None)
    w, h = int(getattr(app, "width", 0) or 0), int(getattr(app, "height", 0) or 0)
    if w > 0 and h > 0:
        from ..format import inputs

        for i, o in enumerate(objs):
            if (
                o.parent is not None
                or o.raw.get("_from_template")
                or o.raw.get("_type_code") in inputs.INPUT_TCS
            ):
                continue
            try:
                l, t, r, b = doc._obj_bbox(i)
            except Exception:  # noqa: BLE001  # swallow-ok: a record without a geometry header
                continue
            if r - l <= 1 and b - t <= 1:
                continue  # an animation point
            if r <= 0 or b <= 0 or l >= w or t >= h:
                rep.findings.append(
                    Finding(
                        INFO,
                        "The object lies outside the page (%d, %d to %d, %d; the page is "
                        "%d by %d): the runtime never shows it." % (l, t, r, b, w, h),
                        i,
                        "",
                        "off_page",
                        label(i),
                    )
                )


def _check_captions(doc, objs, rep: Report, label: Callable[[int], str], measure: Optional[Callable]) -> None:
    """Button captions wider than their button."""
    meas = measure
    if meas is None:
        try:
            from ..format.textmetrics import caption_metrics as meas
        except Exception:  # noqa: BLE001  # swallow-ok: without GDI the caption check is skipped
            meas = None
    if meas is not None:
        for i, o in enumerate(objs):
            if o.kind is not ObjectKind.BUTTON or o.raw.get("_from_template"):
                continue
            text = o.style.text or ""
            if not text.strip() or not o.style.font or not o.style.font_size:
                continue
            attribs = (
                (0x10 if o.style.bold else 0)
                | (0x20 if o.style.italic else 0)
                | (0x40 if o.style.underline else 0)
            )
            try:
                m = meas(
                    text.replace("\r\n", "\n").split("\n"), o.style.font, int(o.style.font_size), attribs
                )
                l, t, r, b = doc._obj_bbox(i)
            except Exception:  # noqa: BLE001  # swallow-ok: a caption GDI cannot measure is skipped
                continue
            if not m:
                continue
            widest = max(m[0]) if m[0] else 0
            inner = (r - l) - 6  # the border and a little padding
            if widest > inner > 0:
                rep.findings.append(
                    Finding(
                        WARNING,
                        "The button's text is %d px wide but the button is %d px: the "
                        "caption is cut. Widen the button or shorten the text." % (widest, r - l),
                        i,
                        "TEXT",
                        "caption_wide",
                        label(i),
                    )
                )


def _check_missing_elements(objs, rep: Report, label: Callable[[int], str]) -> None:
    """Library elements not found in the libraries."""
    for i, o in enumerate(objs):
        ref = o.raw.get("_missing_element")
        if ref:
            rep.findings.append(
                Finding(
                    ERROR,
                    "%s was not found in the libraries: the Graphics Builder and the "
                    "runtime show an X box instead. Add the library, or replace the "
                    "object." % ref,
                    i,
                    "",
                    "missing_element",
                    label(i),
                )
            )


def _check_duplicate_keys(doc, page, objs, rep: Report, label: Callable[[int], str]) -> None:
    """Key sequences set more than once, on an object or on the page."""
    from ..format import inputs as _inputs

    for i, o in enumerate(objs):
        if o.raw.get("_type_code") in _inputs.INPUT_TCS:
            continue
        seen: Dict[str, int] = {}
        for k in input_commands(doc, i).get("keys") or ():
            seq = str(k.get("KEY_SEQ") or "").strip().upper()
            if not seq:
                continue
            seen[seq] = seen.get(seq, 0) + 1
        for seq, n in seen.items():
            if n > 1:
                rep.findings.append(
                    Finding(
                        WARNING,
                        "The key sequence %s is on the object %d times: only the "
                        "first command runs." % (seq, n),
                        i,
                        "KEY_SEQ",
                        "dup_key",
                        label(i),
                    )
                )
    seen = {}
    for k in getattr(page, "keyboard_commands", ()) or ():
        seq = (k.key_sequence or "").strip().upper()
        if seq:
            seen[seq] = seen.get(seq, 0) + 1
    for seq, n in seen.items():
        if n > 1:
            rep.findings.append(
                Finding(
                    WARNING,
                    "The page's key sequence %s is set %d times: only the first command runs." % (seq, n),
                    None,
                    "KEY_SEQ",
                    "dup_key",
                    "(page)",
                )
            )


# --------------------------------------------------------------------------- logic that compiles
#: the condition fields a constant makes pointless: (key, its name, what TRUE does)
_CONDITION_FIELDS = (
    ("VISIBLE", "Hidden when", "the runtime never shows the object"),
    ("DISABLED", "Disable when", "the object is always disabled"),
)
#: Multi-state's condition keys, by the field that holds them
_STATE_KEYS = {
    "COL": ("COLMODE", ("COL_A", "COL_B", "COL_C", "COL_D", "COL_E"), "fill colour"),
    "TXT": ("TXTMODE", ("TXT_A", "TXT_B", "TXT_C", "TXT_D", "TXT_E"), "display value"),
}


def _norm(text) -> str:
    return " ".join(str(text or "").split()).lower()


def constant_value(text: str) -> Optional[bool]:
    """TRUE or FALSE when condition ``text`` is a constant ('1', 'TRUE', 'NOT 0', '(0)'),
    else None (it reads something)."""
    from .. import cicode_lex as L

    toks = [t for t in L.tokenize(text or "") if t.kind != L.COMMENT]
    words = [(t.kind, text[t.start : t.end].strip().upper()) for t in toks]
    words = [w for w in words if not (w[0] == L.PUNCT and w[1] in ("(", ")"))]
    negate = False
    while words and words[0] == (L.OPERATOR_WORD, "NOT"):
        negate = not negate
        words = words[1:]
    if len(words) != 1:
        return None
    kind, word = words[0]
    if kind == L.LABEL and word in ("TRUE", "FALSE"):
        v = word == "TRUE"
    elif kind == L.NUMBER:
        try:
            v = float(word) != 0.0
        except ValueError:
            try:
                v = int(word, 0) != 0
            except ValueError:
                return None
    else:
        return None
    return v != negate


def _in_genie(objs, i: int) -> bool:
    """Object ``i`` lies in a placed genie (its fields are the genie's: set where it is made)."""
    j = objs[i].parent
    while j is not None:
        if objs[j].is_genie:
            return True
        j = objs[j].parent
    return False


def _own_props(doc, i: int) -> Dict[str, str]:
    try:
        vals = FR.own_values(doc, i)
    except Exception:  # noqa: BLE001  # swallow-ok: a record without a value bag has no conditions
        return {}
    out: Dict[str, str] = {}
    for k, v in vals:
        out.setdefault(str(k).upper(), str(v or ""))
    return out


def _check_conditions(doc, objs, rep: Report, label: Callable[[int], str]) -> None:
    """Conditions that are constants, Multi-state conditions repeated or left out, and a
    Hidden when its group has already."""
    props: Dict[int, Dict[str, str]] = {}

    def get(i: int) -> Dict[str, str]:
        if i not in props:
            props[i] = _own_props(doc, i)
        return props[i]

    for i, o in enumerate(objs):
        if o.raw.get("_from_template") or o.kind is ObjectKind.UNKNOWN or _in_genie(objs, i):
            continue
        p = get(i)
        if not p:
            continue
        for key, name, when_true in _CONDITION_FIELDS:
            text = p.get(key, "").strip()
            v = constant_value(text) if text else None
            if v is True:
                rep.findings.append(
                    Finding(
                        WARNING,
                        "%s is always true (%s): %s." % (name, text, when_true),
                        i,
                        key,
                        "constant_condition",
                        label(i),
                    )
                )
            elif v is False:
                rep.findings.append(
                    Finding(
                        INFO,
                        "%s is always false (%s): it never does anything. Clear the field." % (name, text),
                        i,
                        key,
                        "constant_condition",
                        label(i),
                    )
                )
        for mode_key, keys, what in _STATE_KEYS.values():
            if (p.get(mode_key) or "0").strip() != "1":
                continue
            conds = [p.get(k, "").strip() for k in keys]
            last = max((k for k, c in enumerate(conds) if c), default=-1)
            seen: Dict[str, int] = {}
            for k, c in enumerate(conds[: last + 1]):
                letter = "ABCDE"[k]
                if not c:
                    rep.findings.append(
                        Finding(
                            INFO,
                            "Condition %s of the %s is empty but a later one is set: it is always "
                            "false, so the states with %s true never show." % (letter, what, letter),
                            i,
                            keys[k],
                            "state_gap",
                            label(i),
                        )
                    )
                    continue
                n = _norm(c)
                if n in seen:
                    rep.findings.append(
                        Finding(
                            INFO,
                            "Conditions %s and %s of the %s are the same: the states where only one "
                            "of them is true never show." % ("ABCDE"[seen[n]], letter, what),
                            i,
                            keys[k],
                            "state_repeat",
                            label(i),
                        )
                    )
                else:
                    seen[n] = k
        vis = _norm(p.get("VISIBLE"))
        if vis and constant_value(vis) is None:
            j = o.parent
            while j is not None:
                if _norm(get(j).get("VISIBLE")) == vis:
                    rep.findings.append(
                        Finding(
                            INFO,
                            "Hidden when is the same as its group's (%s): the group hides it "
                            "already." % label(j),
                            i,
                            "VISIBLE",
                            "hidden_like_group",
                            label(i),
                        )
                    )
                    break
                j = objs[j].parent


def _check_clicks(doc, objs, rep: Report, label: Callable[[int], str]) -> None:
    """Objects that take clicks with an object over all of them that takes the clicks."""
    from .click_map import click_targets

    try:
        targets = click_targets(doc)
    except Exception:  # noqa: BLE001
        from ..infra.logsetup import note_exception

        note_exception("page_checks._check_clicks")
        return
    by = {t.index: t for t in targets}
    for t in targets:
        if not t.blocked or objs[t.index].raw.get("_from_template"):
            continue
        over = next((by[j] for j in t.above if not by[j].conditional), None)
        rep.findings.append(
            Finding(
                WARNING,
                "A click never reaches it: %s lies over all of it and takes the clicks (unless "
                "the page's Cicode hides it). View > Click Map shows where clicks go."
                % (over.label if over is not None else "another object"),
                t.index,
                "CMD",
                "click_covered",
                label(t.index),
            )
        )


#: a page of more placed genies than this leaves the empty input check out (each takes a
#: few tens of milliseconds)
GENIE_INPUTS_MAX = 400


def _check_genie_inputs(doc, objs, rep: Report, label: Callable[[int], str]) -> None:
    """A placed genie's input left empty that one of its objects' Cicode uses."""
    fn_values = getattr(doc, "genie_values", None)
    fn_inputs = getattr(doc, "genie_inputs", None)
    if fn_values is None or fn_inputs is None:
        return
    recs = doc.parsed.page.objects
    roots = [
        i
        for i, o in enumerate(objs)
        if i < len(recs)
        and o.is_genie
        and recs[i].subtype_size & 0x201 == 0x201
        and not o.raw.get("_from_template")
    ]
    if len(roots) > GENIE_INPUTS_MAX:
        return
    code = EXPRESSION_KEYS | COMMAND_KEYS
    for g in roots:
        try:
            if doc.is_composite_genie(g):
                continue
            empty = {k.lower() for k, v in fn_values(g) if not str(v or "").strip()}
            if not empty:
                continue
            rows = fn_inputs(g)
        except Exception:  # noqa: BLE001
            from ..infra.logsetup import note_exception

            note_exception("page_checks._check_genie_inputs", index=g)
            continue
        from .genie_uses import breaks_when_empty

        for name, _value, uses in rows:
            if name.lower() not in empty:
                continue
            hit = [
                u
                for u in uses
                if u.key.upper() in code
                and breaks_when_empty(u.template, name, u.key.upper() in COMMAND_KEYS)
            ]
            if not hit:
                continue
            u = hit[0]
            one = " ".join(u.value.split())
            more = " and %d more" % (len(hit) - 1) if len(hit) > 1 else ""
            rep.findings.append(
                Finding(
                    WARNING,
                    "The genie's input %s is empty, but %s%s use%s it: %s"
                    % (name, u.label, more, "" if more else "s", one if len(one) <= 60 else one[:57] + "..."),
                    g,
                    name,
                    "genie_input_empty",
                    label(g),
                )
            )


def check_document(doc, measure: Optional[Callable] = None) -> Report:
    """The checks of ``doc`` that need no compiler. ``measure(lines, face, points, attribs)``:
    the button caption metrics (default textmetrics.caption_metrics)."""
    path = getattr(doc, "path", "") or ""
    rep = Report(os.path.splitext(os.path.basename(path))[0])
    page = doc.page
    objs = page.objects
    fields = expression_fields(doc)
    rep.expressions = len(fields)
    rep.fields = fields
    labels: Dict[int, str] = {}

    def label(i: int) -> str:
        if i not in labels:
            labels[i] = object_label(doc, i)
        return labels[i]

    _check_empty_objects(doc, objs, rep, label)
    _check_off_page(doc, page, objs, rep, label)
    _check_captions(doc, objs, rep, label, measure)
    _check_missing_elements(objs, rep, label)
    _check_duplicate_keys(doc, page, objs, rep, label)
    _check_conditions(doc, objs, rep, label)
    _check_clicks(doc, objs, rep, label)
    _check_genie_inputs(doc, objs, rep, label)
    rep.findings.sort(
        key=lambda f: ({ERROR: 0, WARNING: 1, INFO: 2}.get(f.level, 3), -1 if f.index is None else f.index)
    )
    return rep


def engine_findings(doc, f: ExpressionField, result: dict) -> List[Finding]:
    """Findings of one engine check of field ``f``: its errors, and its warnings (code = their
    kind: 'tag', 'page')."""
    out: List[Finding] = []
    if result.get("error"):
        return out
    for w in result.get("warnings") or ():
        out.append(
            Finding(
                WARNING,
                "%s: %s" % (f.label, w.get("message") or ""),
                f.index,
                f.key,
                str(w.get("kind") or "compile"),
                object_label(doc, f.index),
                str(w.get("name") or ""),
                int(w.get("column") or 0),
            )
        )
    for e in result.get("errors") or ():
        col = e.get("column") or 0
        where = " at column %d" % col if col else ""
        msg = e.get("message") or e.get("name") or "does not compile"
        out.append(
            Finding(
                ERROR,
                "%s does not compile%s: %s (%s)"
                % (f.label, where, msg, f.text if len(f.text) <= 60 else f.text[:57] + "..."),
                f.index,
                f.key,
                "compile",
                object_label(doc, f.index),
            )
        )
    return out


def count_summary(count: Callable[[str], int]) -> str:
    """'2 errors, 1 warning, 3 notes' from ``count(level)`` (the levels with none left out)."""
    parts = []
    for lvl, one, many in (
        (ERROR, "error", "errors"),
        (WARNING, "warning", "warnings"),
        (INFO, "note", "notes"),
    ):
        n = count(lvl)
        if n:
            parts.append("%d %s" % (n, one if n == 1 else many))
    return ", ".join(parts)
