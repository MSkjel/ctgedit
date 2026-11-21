"""Keyboard navigation between panels."""

from __future__ import annotations

from typing import List, Optional

from PySide6 import QtCore, QtWidgets

#: (name, label, dock attribute) of the Go to commands, in key order (Alt+0 ...)
PANELS = (
    ("page", "Go to Page", ""),
    ("objects", "Go to Objects", "dock_objects"),
    ("library", "Go to Library", "dock_library"),
    ("tags", "Go to Tags", "dock_tags"),
    ("props", "Go to Properties", "dock_props"),
    ("history", "Go to History", "dock_history"),
)

_REASON = QtCore.Qt.ShortcutFocusReason


def _focusable(w: QtWidgets.QWidget) -> bool:
    return w.isVisible() and w.isEnabled() and bool(w.focusPolicy() & QtCore.Qt.TabFocus)


def first_field(container: QtWidgets.QWidget) -> Optional[QtWidgets.QWidget]:
    """The first widget of ``container`` the Tab key would reach (its tab order)."""
    w = container.nextInFocusChain()
    seen = 0
    while w is not None and w is not container and seen < 5000:
        seen += 1
        if (
            container.isAncestorOf(w)
            and _focusable(w)
            and not isinstance(w, (QtWidgets.QScrollBar, QtWidgets.QAbstractButton))
        ):
            return w
        w = w.nextInFocusChain()
    return None


def _show_dock(dock) -> None:
    if dock is None:
        return
    if dock.isHidden():
        dock.show()
    dock.raise_()


def _target(win, name: str) -> Optional[QtWidgets.QWidget]:
    if name == "page":
        return win.view
    if name == "objects":
        trees = [t for t in win.objects_dock.findChildren(QtWidgets.QTreeWidget) if t.isVisible()]
        return trees[0] if trees else win.objects_dock
    if name == "library":
        return win.library_dock.search_field()
    if name == "tags":
        fields = [f for f in win.tags_dock.findChildren(QtWidgets.QLineEdit) if f.isVisible()]
        return fields[0] if fields else win.tags_dock
    if name == "props":
        return first_field(win.props) or win.props
    if name == "history":
        return win.history_dock.list
    return None


def go_to(win, name: str) -> bool:
    """Show the panel ``name`` (PANELS) and put the focus in it; 'page' = the canvas."""
    attr = dict((n, d) for n, _l, d in PANELS).get(name, "")
    if attr:
        _show_dock(getattr(win, attr, None))
    if name == "tags" and hasattr(win.tags_dock, "focus_search"):
        win.tags_dock.focus_search()
        return True
    if name == "library" and hasattr(win.library_dock, "focus_search"):
        win.library_dock.focus_search()
        return True
    w = _target(win, name)
    if w is None:
        return False
    w.setFocus(_REASON)
    if isinstance(w, QtWidgets.QLineEdit):
        w.selectAll()
    return True


def _left_dock(win):
    """The left panel the user sees (of Objects, Library and Tags)."""
    docks = [d for d in (win.dock_objects, win.dock_library, win.dock_tags) if d.isVisible()]
    for d in docks:
        if not d.visibleRegion().isEmpty():
            return d
    return docks[0] if docks else None


def stops(win) -> List[str]:
    """The stops of Next Panel, in order (the ones shown now)."""
    out = ["page"]
    if win.dock_props.isVisible():
        out.append("props")
    left = _left_dock(win)
    if left is not None:
        out.append({win.dock_objects: "objects", win.dock_library: "library", win.dock_tags: "tags"}[left])
    if win.doc_tabs.isVisible():
        out.append("tabs")
    return out


def current_stop(win) -> str:
    fw = QtWidgets.QApplication.focusWidget()
    if fw is None:
        return ""
    if fw is win.view or win.view.isAncestorOf(fw):
        return "page"
    if fw is win.doc_tabs:
        return "tabs"
    for name, _label, attr in PANELS[1:]:
        dock = getattr(win, attr, None)
        if dock is not None and (fw is dock or dock.isAncestorOf(fw)):
            return name
    return ""


def cycle(win, step: int = 1) -> str:
    """Move the focus to the next (``step`` 1) or previous (-1) stop; the stop reached."""
    order = stops(win)
    cur = current_stop(win)
    k = order.index(cur) if cur in order else (-1 if step > 0 else 0)
    nxt = order[(k + step) % len(order)]
    if nxt == "tabs":
        win.doc_tabs.setFocus(_REASON)
    else:
        go_to(win, nxt)
    return nxt
