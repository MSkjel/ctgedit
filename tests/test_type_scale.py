"""The UI type scale."""

import ast
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit", "ui")

_CSS = re.compile(r"font-size\s*:\s*\d|font\s*:[^;'\"]*?\d+(?:\.\d+)?\s*(?:px|pt)")
_CALLS = ("setPixelSize", "setPointSize", "setPointSizeF")

#: today's literal font sizes per file (the canvas, the ribbon, the start page and the
#: dialogs of other packages): may only shrink
OFFENDERS = {
    "dialog/help_window.py": 5,
    "panels/objects_dock.py": 1,
    "panels/properties_parts.py": 1,
    "ribbon.py": 1,
    "panels/start_page.py": 4,
}


def counts():
    out = {}
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
            n = 0
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    n += len(_CSS.findall(node.value))
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _CALLS
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    n += 1
            if n:
                out[rel] = n
    return out


def test_no_new_literal_font_sizes():
    got = counts()
    over = [
        "ui/%s: %d literal font sizes (allowed %d)" % (f, n, OFFENDERS.get(f, 0))
        for f, n in sorted(got.items())
        if n > OFFENDERS.get(f, 0)
    ]
    assert not over, "use theme.font(role) / theme.type_qss(role):\n" + "\n".join(over)
    # (an entry that went down is lowered here, so it cannot creep back)
    fewer = [
        "%s: %d now, %d listed" % (f, got.get(f, 0), n) for f, n in OFFENDERS.items() if got.get(f, 0) < n
    ]
    assert not fewer, "lower OFFENDERS: " + ", ".join(fewer)


def test_the_scale_and_its_helpers():
    from ctgedit.ui.style import theme as T

    assert T.TYPE == {
        "caption": (11, 400),
        "body": (12, 400),
        "body_strong": (12, 600),
        "subtitle": (14, 600),
        "title": (18, 600),
        "display": (24, 600),
    }
    assert T.SPACE == (2, 4, 8, 12, 16, 24)
    assert T.type_qss("title") == "font-size: 18px; font-weight: 600;"
    assert T.font("caption").pixelSize() == round(11 * T.ui_scale())


@pytest.fixture()
def theme():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.style import theme as T

    snap = dict(
        sheet=app.styleSheet(),
        state=dict(T._state),
        colors=dict(T.COLORS),
        font=QtGui.QFont(app.font()),
        palette=QtGui.QPalette(app.palette()),
    )
    yield T
    T.apply_theme(app, "dark", 1.0)
    T.COLORS.clear()
    T.COLORS.update(snap["colors"])
    T._state.clear()
    T._state.update(snap["state"])
    app.setStyleSheet(snap["sheet"])
    app.setFont(snap["font"])
    app.setPalette(snap["palette"])


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_group_box_titles_are_body_strong(theme, scale):
    app = QtWidgets.QApplication.instance()
    theme.apply_theme(app, "dark", scale)
    assert theme.font("body_strong").pixelSize() == round(12 * scale)
    assert theme.sp(2) == round(8 * scale)
    sheet = app.styleSheet()
    assert re.search(r"QGroupBox \{[^}]*font-size: %dpx;[^}]*font-weight: 600;" % round(12 * scale), sheet)
    assert re.search(
        r"QWidget#DockTitleBar QLabel \{ font-size: %dpx;\s*font-weight: 600;" % round(12 * scale), sheet
    )
    g = QtWidgets.QGroupBox("Look")
    QtWidgets.QVBoxLayout(g).addWidget(QtWidgets.QCheckBox("child"))
    g.show()
    app.processEvents()
    assert g.font().weight() == QtGui.QFont.Weight.DemiBold
    assert g.font().pixelSize() == round(12 * scale)
    child = g.findChild(QtWidgets.QCheckBox)
    assert child.font().weight() == QtGui.QFont.Weight.Normal
    g.deleteLater()


def test_settings_headings_use_the_scale():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.prefs.settings_pages import Page
    from ctgedit.ui.style import theme as T

    p = Page("Canvas")
    # (the sheet as written: the hook's property once a theme ran, else the widget's own)
    assert T.type_qss("title") in (p.heading.property("ctgQss") or p.heading.styleSheet())
    s = p.section("Grid")
    assert T.type_qss("body_strong") in (s.heading.property("ctgQss") or s.heading.styleSheet())
