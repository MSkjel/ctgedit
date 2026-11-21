"""UI painters take colours from the theme."""

import ast
import re
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit", "ui")

#: data colours, by file: literal -> why it is not a theme colour
DATA = {
    "colour_picker.py": {
        "#ffffff": "the checkerboard / a white swatch (data)",
        "#c8c8c8": "the checkerboard (data)",
        "#000000": "the 'no colour' cross on a swatch (data)",
        "#555555": "a swatch's frame: neutral on any colour",
    },
    "dialog/colour_dialogs.py": {
        "#555555": "a swatch's frame: neutral on any colour",
        "128,128,128,255": "the colour wheel's shading (data)",
        "128,128,128,0": "the colour wheel's shading (data)",
    },
    "objprops/fill.py": {"#555555": "a swatch's frame: neutral on any colour"},
    "panels/properties_parts.py": {"#555555": "a colour swatch's frame: neutral on any colour"},
    "prefs/settings_dialog.py": {
        "#ffffff": "the page in the theme tiles and the preview (page art)",
        "#808080": "the page edge in the preview, as the canvas draws it (page art)",
        "0,0,0,28": "the grid lines on the preview's white page (page art)",
        "0,0,0,90": "the grid dots on the preview's white page (page art)",
        "#3a6ea5": "a sample rectangle on the preview's page (page art)",
        "#9fc4e8": "a sample rectangle on the preview's page (page art)",
    },
    "dialog/bitmap_editor.py": {
        "255,255,255": "picture pixels / the transparency checkerboard (data)",
        "0,0,0": "picture pixels / the pen cursor over them (data)",
        "214,214,214": "the transparency checkerboard (data)",
        "0,0,0,40": "the pixel lattice over a picture (data)",
    },
    "dialog/picture_import.py": {
        "255,255,255": "the transparency checkerboard (data)",
        "204,204,204": "the transparency checkerboard (data)",
    },
    "browser/preview.py": {"#d4d0c8": "a page's default colour (CtDraw's)"},
    "window/view.py": {"#d4d0c8": "a page's default colour (CtDraw's)"},
    "window/library.py": {"255,255,255": "a picture flattened on white for a JPEG (data)"},
    "simulate/alarm_panels.py": {"#202020": "a swatch's frame"},
    "simulate/canvas.py": {
        "220,30,30": "the simulator's error mark over the page (page art)",
        "230,140,0": "the simulator's warning mark over the page (page art)",
        "255,255,255": "the mark's text",
    },
    "simulate/perf_view.py": {
        "110,118,128": "a flame chart block (chart data)",
        "85,90,98": "a flame chart block (chart data)",
        "0,0,0,90": "a flame chart block's edge (chart data)",
        "20,20,20": "the label on a flame chart block (chart data)",
    },
}
#: files whose literals other packages replace (may only shrink)
TEMPORARY = {"editor/navbar.py"}


def _literal(call: ast.Call):
    """The QColor literal of ``call`` ('#rrggbb' or 'r,g,b[,a]'), or None."""
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    if name != "QColor" or not call.args:
        return None
    a = call.args
    if (
        len(a) == 1
        and isinstance(a[0], ast.Constant)
        and isinstance(a[0].value, str)
        and a[0].value.startswith("#")
    ):
        return a[0].value.lower()
    if len(a) in (3, 4) and all(isinstance(x, ast.Constant) and isinstance(x.value, int) for x in a):
        return ",".join(str(x.value) for x in a)
    return None


def _in_except(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            for sub in ast.walk(node):
                out.add(id(sub))
    return out


def literals():
    for dp, _dn, fns in os.walk(ROOT):
        if "__pycache__" in dp:
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if rel == "style/theme.py":
                continue
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            skip = _in_except(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and id(node) not in skip:
                    lit = _literal(node)
                    if lit is not None:
                        yield rel, lit, node.lineno


def test_no_colour_literals_in_painters():
    bad = []
    for rel, lit, line in literals():
        if rel in TEMPORARY or lit in DATA.get(rel, {}):
            continue
        bad.append("ui/%s:%d QColor(%s)" % (rel, line, lit))
    assert not bad, "use theme.color(role) / theme.fixed(name):\n" + "\n".join(bad)


def test_the_allow_lists_name_only_what_is_there():
    found = {}
    for rel, lit, _line in literals():
        found.setdefault(rel, set()).add(lit)
    stale = ["%s %s" % (f, lit) for f, lits in DATA.items() for lit in lits if lit not in found.get(f, set())]
    assert not stale, "no longer there, take them off DATA: %s" % stale


def test_library_no_preview_reads_on_its_light_well():
    from ctgedit.ui.style import theme as T

    assert T.contrast(T.fixed("well_text"), T.fixed("well")) >= 4.5


# --------------------------------------------------------------------- stylesheet text
CTGEDIT = os.path.dirname(ROOT)
_RULE = re.compile(r"\{[^{}]*\}")
_ON_SELECTION = re.compile(
    r"background(?:-color)?\s*:\s*(?:%\(selection(?:_hover)?\)s|#2b4a6f|#35598a)", re.I
)
_HARD_WHITE = re.compile(r"(?<![-\w])color\s*:\s*(?:#fff\b|#ffffff\b|white\b)", re.I)


def _sources():
    for dp, _dn, fns in os.walk(CTGEDIT):
        if "__pycache__" in dp:
            continue
        for fn in fns:
            if fn.endswith(".py"):
                path = os.path.join(dp, fn)
                rel = os.path.relpath(path, CTGEDIT).replace(os.sep, "/")
                yield rel, open(path, encoding="utf-8").read()


def _white_on_selection(text):
    return [
        m.group(0)
        for m in _RULE.finditer(text)
        if _ON_SELECTION.search(m.group(0)) and _HARD_WHITE.search(m.group(0))
    ]


def test_the_white_on_selection_scan_finds_the_old_library_rule():
    old = "QTreeWidget#libTree::item:selected { background:%(selection)s; color:#ffffff; }"
    assert _white_on_selection(old)
    assert not _white_on_selection(old.replace("#ffffff", "%(sel_text)s"))
    assert not _white_on_selection("QPushButton { background:%(accent)s; color:#ffffff; }")


def test_no_hard_white_text_on_the_selection():
    """White never maps to another colour, and the light selection is pale (#cfe0f7):
    text on the selection is %(sel_text)s (or %(on_selection)s)."""
    bad = [
        "%s: %s" % (rel, rule.replace("\n", " ")[:100])
        for rel, text in _sources()
        for rule in _white_on_selection(text)
    ]
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------- field edges, ring rules
#: the field kinds edged in control_edge (the application sheet's field rule)
_FIELDS = r"(?:QLineEdit|QPlainTextEdit|QTextEdit|QSpinBox|QDoubleSpinBox|QAbstractSpinBox|QComboBox)"
#: a panel edge ('border' and its old value) as a field's edge: about 1.4:1, not 3:1
_FAINT_EDGE = re.compile(r"(?<![-\w])border(?:-color)?\s*:[^;}]*(?:#3a3f46|#343a42|%\(border\)s)", re.I)
_SHEET_RULE = re.compile(r"([^{}]*)\{([^{}]*)\}")
_BORDER = re.compile(r"(?<![-\w])border\s*:", re.I)
_KBD = '[kbdFocus="true"]'


def _sheets():
    """(file, line, text) of every string constant with a stylesheet rule in it."""
    for rel, text in _sources():
        if not rel.startswith("ui/"):
            continue
        for node in ast.walk(ast.parse(text)):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "{" in node.value
                and "}" in node.value
            ):
                yield rel, node.lineno, node.value


def _selectors(sheet):
    """(the last compound of each selector, the rule's body) of every rule in ``sheet``."""
    for m in _SHEET_RULE.finditer(sheet):
        for sel in m.group(1).split(","):
            parts = sel.split()
            if parts:
                yield parts[-1], m.group(2)


def _faint_fields(sheet):
    pat = re.compile(r"^%s(?:[#.][\w-]+)*$" % _FIELDS)
    return [sel for sel, body in _selectors(sheet) if pat.match(sel) and _FAINT_EDGE.search(body)]


def test_the_faint_field_scan_finds_the_old_rules():
    old = "QLineEdit, QSpinBox { background: #1f2226; border: 1px solid #3a3f46; }"
    assert _faint_fields(old) == ["QLineEdit", "QSpinBox"]
    assert _faint_fields("QLineEdit#libSearch { border:1px solid %(border)s; }")
    assert not _faint_fields(old.replace("#3a3f46", "#6e7681"))
    # a disabled field, a combo's popup list: the panel edge is right there
    assert not _faint_fields(
        "QLineEdit:disabled { border-color: %(border)s; }"
        "QComboBox QAbstractItemView { border: 1px solid #3a3f46; }"
    )


def test_fields_are_edged_in_control_edge():
    """A text field, spin box or combo box is edged in control_edge (3:1 on the window),
    not in the panel edge ('border', about 1.4:1)."""
    bad = ["ui/%s:%d %s" % (rel, line, sel) for rel, line, sheet in _sheets() for sel in _faint_fields(sheet)]
    assert not bad, "write control_edge (#6e7681 / %(control_edge)s):\n" + "\n".join(bad)


def _ringed_kinds():
    """The kinds the application sheet rings with a border ([kbdFocus="true"] rules)."""
    from ctgedit.ui.style import theme as T

    kinds = set()
    for sel, body in _selectors(T._QSS):
        m = re.match(r"^(Q\w+)\[kbdFocus=\"true\"\]$", sel)
        if m and _BORDER.search(body):
            kinds.add(m.group(1))
    return kinds


def _unringed(sheet, kinds):
    """The selectors of ``sheet`` that give one of ``kinds`` a border without a ring rule
    of their own: a widget's sheet beats the application's whatever the specificity, so
    the application's ring would never show on them."""
    pat = re.compile(r"^(%s)((?:[#.][\w-]+)*)$" % "|".join(sorted(kinds)))
    ringed = {sel[: -len(_KBD)] for sel, _b in _selectors(sheet) if sel.endswith(_KBD)}
    return [
        sel
        for sel, body in _selectors(sheet)
        if pat.match(sel) and _BORDER.search(body) and sel not in ringed
    ]


def test_the_ring_scan_finds_a_bordered_button_without_its_ring():
    kinds = _ringed_kinds()
    assert {"QToolButton", "QPushButton", "QTreeView", "QListView"} <= kinds
    old = "QToolButton { border: 1px solid #3a3f46; } QToolButton:hover { border-color: #4da3ff; }"
    assert _unringed(old, kinds) == ["QToolButton"]
    assert not _unringed(old + ' QToolButton[kbdFocus="true"] { border: 2px solid #4da3ff; }', kinds)
    # a bare ring rule loses to an id rule: QToolButton#card needs its own
    assert _unringed(
        'QToolButton#card { border: none; } QToolButton[kbdFocus="true"] { border: 2px solid #4da3ff; }',
        kinds,
    )


def test_bordered_buttons_and_views_in_widget_sheets_ring_themselves():
    """The focus ring shows on every kind the application sheet rings, also
    inside a panel whose own sheet gives that kind a border."""
    kinds = _ringed_kinds()
    bad = [
        "ui/%s:%d %s" % (rel, line, sel)
        for rel, line, sheet in _sheets()
        if rel != "ui/style/theme.py"
        for sel in _unringed(sheet, kinds)
    ]
    assert not bad, 'add a [kbdFocus="true"] rule with the accent border:\n' + "\n".join(bad)
