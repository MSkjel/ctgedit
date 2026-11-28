"""Undo step names."""

import ast
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")

#: the calls that take an undo step's label (first argument or label=)
LABEL_CALLS = {
    "_apply",
    "batch",
    "Command",
    "_push_step",
    "_body_edit",
    "_multi_body_edit",
    "_structural",
    "_record_edit",
    "_register_edit",
}
#: what a format placeholder in a label stands for when the test fills it
SAMPLES = {"%s": ("VISIBLE", "METADATA_Speed", "x", "Thick", "DOWN_CMD"), "%d": ("3",), "{}": ("VISIBLE",)}
#: an internal key left in a title: an upper-case word joined by '_' or one of 4+ letters
KEY = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b[A-Z]{4,}\b")


def _lit(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return [n.value]
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod):
        return _lit(n.left)
    if isinstance(n, ast.JoinedStr):
        return ["".join(v.value if isinstance(v, ast.Constant) else "{}" for v in n.values)]
    if isinstance(n, ast.IfExp):
        return _lit(n.body) + _lit(n.orelse)
    return []


def labels():
    """Every undo label the code writes: the label argument of the edit helpers, and the
    default of each function parameter named 'label' (the callers' labels)."""
    out = set()
    for dp, _dn, fns in os.walk(ROOT):
        if "__pycache__" in dp:
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(dp, fn), encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    a = node.args
                    names = [x.arg for x in a.args + a.kwonlyargs]
                    defs = [None] * (len(a.args) - len(a.defaults)) + list(a.defaults) + list(a.kw_defaults)
                    for name, d in zip(names, defs):
                        if name == "label" and d is not None:
                            out.update(_lit(d))
                elif isinstance(node, ast.Call):
                    f = node.func
                    name = getattr(f, "attr", None) or getattr(f, "id", None)
                    if name in LABEL_CALLS and node.args:
                        out.update(_lit(node.args[0]))
                    if name in LABEL_CALLS:
                        for kw in node.keywords:
                            if kw.arg == "label":
                                out.update(_lit(kw.value))
    return {s for s in out if s and not s.startswith("%s (")}


def _filled(label):
    """``label`` with each placeholder filled by every sample."""
    out = [label]
    for ph, samples in SAMPLES.items():
        out = [x.replace(ph, s, 1) for x in out for s in samples] if any(ph in x for x in out) else out
    return out


def test_every_step_label_has_a_title_without_an_internal_key():
    from ctgedit.ui.names import step_title

    found = labels()
    # (the walk finds the labels the finding names)
    for want in (
        "move",
        "align",
        "property %s",
        "update genies from library",
        "delete",
        "duplicate" if "duplicate" in found else "move",
    ):
        assert want in found, want
    bad = []
    for lab in sorted(found):
        for text in _filled(lab):
            t = step_title(text)
            if KEY.search(t) or "%" in t or not (t[:1].isupper() or t[:1].isdigit()):
                bad.append("%r -> %r" % (text, t))
    assert not bad, "\n".join(bad)


def test_titles_are_title_case_with_field_names():
    from ctgedit.ui.names import step_title

    assert step_title("move") == "Move"
    assert step_title("align") == "Align"
    assert step_title("update genies from library") == "Update Genies from Library"
    assert step_title("property VISIBLE") == "Hidden When"
    assert step_title("property CMD") == "Command"
    assert step_title("remove property DOWN_CMD") == "Remove Down Command"
    assert step_title("property METADATA_Speed") == "Metadata Speed"
    assert step_title("change AN") == "Change AN"
    assert step_title("3d effect") == "3D Effect"
    assert step_title("style Thick") == "Style Thick"
    assert step_title("template %Tag% re-applied") == "Template Re-applied"
    assert step_title("") == "Edit"


def test_the_menu_and_the_status_bar_use_the_titles(fx_page):
    """Edit > Undo names the step in Title Case; the status bar says 'Undid Move.'. On the
    fake install's FxShapes page (FxMain), moving its rectangle FxRect."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    # (a page of its own: a '!' page made from a super genie asks a question on open)
    dst = fx_page("FxShapes")
    w = MainWindow()
    try:
        w.open_document(str(dst))
        r = w.doc.find_objects_by_name("FxRect")[0]
        assert r in w.doc.page.roots()
        w.doc.move_objects([r], 4, 0)
        w._refresh()
        assert w.act_undo.text() == "Undo Move" and w.doc._undo[-1].label == "move"
        w.undo()
        assert w.statusBar().currentMessage() == "Undid Move."
        assert w.act_redo.text() == "Redo Move"
        w.redo()
        assert w.statusBar().currentMessage() == "Redid Move."
    finally:
        # (hidden, not closed: a close asks about the unsaved changes)
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()
