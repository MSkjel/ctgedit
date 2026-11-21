"""The canvas right-click menu, built from sections other modules can add to."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import os
from typing import Callable, Dict, NamedTuple

from PySide6 import QtCore, QtWidgets


class Section(NamedTuple):
    name: str
    order: int
    builder: Callable


#: the registered sections by name
_SECTIONS: Dict[str, Section] = {}


def register_section(name: str, order: int, builder: Callable) -> None:
    """Add (or replace) the section ``name``: ``builder(win, menu, ctx)`` adds its entries."""
    _SECTIONS[name] = Section(name, int(order), builder)


def unregister_section(name: str) -> None:
    _SECTIONS.pop(name, None)


def sections() -> list:
    """The sections in menu order."""
    return sorted(_SECTIONS.values(), key=lambda s: (s.order, s.name))


def _hits_under(view, viewpos) -> list:
    """The page indices of the items under ``viewpos``, topmost first, each once."""
    from ...render.scene import PAGE_INDEX_ROLE

    out, seen = [], set()
    try:
        items = view.items(viewpos)
    except RuntimeError:
        return out
    for item in items:
        d = item.data(PAGE_INDEX_ROLE)
        if isinstance(d, int) and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def make_context(win, global_pos: QtCore.QPoint, indices: list) -> dict:
    """The ``ctx`` the section builders get."""
    view = getattr(win, "view", None)
    scene_pos, hits, guide = None, [], None
    if view is not None:
        try:
            vp = view.viewport().mapFromGlobal(global_pos)
            scene_pos = view.mapToScene(vp)
            hits = _hits_under(view, vp)
            guide = view._guide_at(vp)
        except RuntimeError:  # swallow-ok: a view being destroyed, no point then
            scene_pos, hits, guide = None, [], None
    doc = getattr(win, "doc", None)
    units = set()
    if doc is not None:
        n = len(doc.page.objects)
        units = {doc.root_of(i) for i in indices if 0 <= i < n}
    return {
        "indices": list(indices),
        "units": units,
        "scene_pos": scene_pos,
        "hits_under": hits,
        "guide": guide,
    }


def build_canvas_menu(win, global_pos: QtCore.QPoint, indices: list) -> QtWidgets.QMenu:
    """The canvas's right-click menu for the selected roots ``indices`` (not shown)."""
    menu = QtWidgets.QMenu(win)
    ctx = make_context(win, global_pos, indices)
    for sec in sections():
        before = len(menu.actions())
        if before and not menu.actions()[-1].isSeparator():
            sep = menu.addSeparator()
        else:
            sep = None
        try:
            sec.builder(win, menu, ctx)
        except Exception as e:  # noqa: BLE001 -- one section must not cost the menu
            from ...infra import logsetup

            logsetup.note_exception("context_menu.%s" % sec.name, e)
        if sep is not None and len(menu.actions()) == before + 1:
            menu.removeAction(sep)  # the section added nothing
    while menu.actions() and menu.actions()[-1].isSeparator():
        menu.removeAction(menu.actions()[-1])
    return menu


def show_canvas_menu(win, global_pos: QtCore.QPoint, indices: list) -> None:
    menu = build_canvas_menu(win, global_pos, indices)
    # with Settings > Canvas 'Paste puts the objects: At the pointer', the menu's Paste
    # lands where the menu was opened (MainWindow.paste_point)
    point = None
    view = getattr(win, "view", None)
    if view is not None:
        try:
            sp = view.mapToScene(view.viewport().mapFromGlobal(global_pos))
            point = (int(round(sp.x())), int(round(sp.y())))
        except RuntimeError:  # swallow-ok: a view being destroyed, no point then
            point = None
    win._menu_paste_point = point
    try:
        menu.exec(global_pos)
    finally:
        win._menu_paste_point = None


# ------------------------------------------------------------------ the built-in sections
def _guide(win, menu, ctx) -> None:
    """A right-click on a guideline offers it: Guideline Position (Guidelines
    Setup on it) and Delete Guideline, where the page's Objects menu knows nothing of it."""
    g = ctx.get("guide")
    if not g:
        return
    axis, pos = g[0], int(round(g[1]))
    a = menu.addAction("Guideline Position…")
    a.setStatusTip(
        "Open Guidelines Setup on the %s guideline at %d px."
        % ("vertical" if axis == "v" else "horizontal", pos)
    )
    a.triggered.connect(lambda _=False: win.guideline_setup_at(axis, pos))
    d = menu.addAction("Delete Guideline")
    d.setStatusTip(
        "Remove the %s guideline at %d px (dropping it at the page edge does the "
        "same)." % ("vertical" if axis == "v" else "horizontal", pos)
    )
    d.triggered.connect(lambda _=False: win.delete_guideline(axis, pos))


def _quick(win, menu, ctx) -> None:
    """The quick row: a mini toolbar atop the menu, of the commands of
    Settings > Ribbon > Right-click menu (Align Left, Centres and Right, Group, Bring to
    Front, Send to Back; none: no row)."""
    if not ctx["indices"]:
        return
    from ..prefs.window_options import menu_quick_actions

    actions = menu_quick_actions(win)
    if not actions:
        return
    row = QtWidgets.QWidget(menu)
    rl = QtWidgets.QHBoxLayout(row)
    rl.setContentsMargins(_sp(1), _sp(0), _sp(1), _sp(0))
    rl.setSpacing(1)
    for a in actions:
        b = QtWidgets.QToolButton(row)
        b.setDefaultAction(a)
        b.setAutoRaise(True)
        b.setIconSize(QtCore.QSize(16, 16))
        b.clicked.connect(menu.close)
        rl.addWidget(b)
    wa = QtWidgets.QWidgetAction(menu)
    wa.setDefaultWidget(row)
    menu.addAction(wa)


def _object(win, menu, ctx) -> None:
    """What the selection is: a genie's parameters, its definition, a text's font, a
    picture's commands; then Cut, Copy, Duplicate and Delete."""
    indices = ctx["indices"]
    if not indices:
        return
    # selection-typed ordering: genies lead with their parameters
    traits = win._classify_selection()
    one_root = len(ctx["units"]) == 1
    defn = win._definition_of(indices[0]) if one_root else None
    comp = win._composite_root(indices[0]) if one_root else None
    if comp is not None:
        # CtDraw's 'Edit Composite Genie' (help 1172220): the Presentation Options; OK
        # composes the new options
        a = menu.addAction("Edit Composite Genie…")
        a.setStatusTip("Change the composite genie's presentation options")
        a.triggered.connect(lambda _=False, r=comp: win._edit_genie(r, win.doc.page.objects[r]))
        a = menu.addAction("Update Composite Genie")
        a.setStatusTip(
            "Compose this composite genie again with the newest template "
            "and content genies, keeping its options"
        )
        a.triggered.connect(lambda _=False, r=comp: win._update_composite(r))
    elif "GENIE" in traits:
        menu.addAction(win.act_genie_params)
        menu.addAction(win.act_replace_genie)
    if defn is not None:
        a = menu.addAction(
            "Edit Definition (%s.%s)" % (os.path.splitext(os.path.basename(defn[0]))[0], defn[1])
        )
        a.triggered.connect(lambda _=False, d=defn: win._open_element(*d))
    if "GENIE" in traits or defn is not None or comp is not None:
        menu.addSeparator()
    elif "TEXT" in traits:
        if len(win._sel) == 1 and win._inplace_refusal(win._sel[0]) is None:
            menu.addAction(win.act_edit_text)
        menu.addAction(win.act_font_dialog)
        menu.addSeparator()
    if len(win._sel) == 1 and win._picture_target(quiet=True)[0] is not None:
        for a in (
            win.act_bitmap_editor,
            win.act_replace_picture,
            win.act_export_picture,
            win.act_resample_picture,
        ):
            if a.isEnabled():
                menu.addAction(a)
        menu.addSeparator()
    for a in (win.act_cut, win.act_copy, win.act_dup, win.act_del):
        menu.addAction(a)


def _arrange(win, menu, ctx) -> None:
    """The stacking order, Group / Ungroup and the object's properties."""
    if not ctx["indices"]:
        return
    for a in (win.act_front, win.act_fwd, win.act_bwd, win.act_back):
        menu.addAction(a)
    menu.addSeparator()
    menu.addAction(win.act_group)
    menu.addAction(win.act_ungroup)
    menu.addSeparator()
    menu.addAction(win.act_obj_props)


def _clipboard(win, menu, ctx) -> None:
    menu.addAction(win.act_paste)


def _page(win, menu, ctx) -> None:
    """The page: fit, its properties, its template, Simulate."""
    menu.addAction(win.act_zoom_fit)
    menu.addAction(win.act_zoom_all)
    menu.addSeparator()
    menu.addAction(win.act_page_props)
    if not ctx["indices"] and win._page_has_template():
        menu.addAction(win.act_edit_template)
    if win.act_simulate.isEnabled():
        menu.addAction(win.act_simulate)


#: the icon of each kind of object in the Select list (the Objects panel's)
_KIND_ICONS = {
    "line": "line",
    "rectangle": "rectangle",
    "ellipse": "ellipse",
    "polygon": "polygon",
    "pipe": "pipe",
    "text": "text",
    "number": "number",
    "button": "button",
    "symbol": "symbol",
    "symbol_set": "symbol",
    "group": "group",
    "genie": "group",
    "composite_genie": "group",
}


def _locked(win, menu, ctx) -> None:
    """A right-click on a locked object: 'Unlock Genie AN 80' first."""
    if ctx["indices"] or getattr(win, "doc", None) is None:
        return
    for h in ctx["hits_under"]:
        root = win._locked_root_of(h)
        if root is not None:
            a = menu.addAction("Unlock %s" % win._unit_label(root))
            a.setIcon(win.act_unlock.icon())
            a.setStatusTip("Let this locked object be selected and moved again")
            a.triggered.connect(lambda _=False, r=root: win._unlock_root(r))
            return


def _stack(win, menu, ctx) -> None:
    """Two or more objects under the pointer: a Select list of them, the topmost first;
    the row under the mouse is outlined on the page, a click selects that object."""
    view = getattr(win, "view", None)
    sp = ctx.get("scene_pos")
    if view is None or sp is None or getattr(win, "doc", None) is None:
        return
    units = view.units_under(view.mapFromScene(sp))
    if len(units) < 2:
        return
    from ..style import theme

    sub = menu.addMenu("Select")
    sub.setIcon(win.act_sel_all.icon())
    objs = win.doc.page.objects
    for unit in units:
        root = min(unit)
        if not 0 <= root < len(objs):
            continue
        a = sub.addAction(win._unit_row_text(root))
        a.setIcon(theme.icon(_KIND_ICONS.get(objs[root].kind.value, "unknown")))
        if root in ctx["units"]:
            # (selected now: checked, and in bold, since a menu row with an icon may not
            # show its check mark)
            a.setCheckable(True)
            a.setChecked(True)
            f = a.font()
            f.setBold(True)
            a.setFont(f)
        a.hovered.connect(lambda u=list(unit): view.set_highlight(u))
        a.triggered.connect(lambda _=False, u=list(unit): view.select_units([u], reveal=False))
    sub.aboutToHide.connect(lambda: view.set_highlight([]))
    menu.aboutToHide.connect(lambda: view.set_highlight([]))


def _same(win, menu, ctx) -> None:
    """Select Same (Edit > Select > Select Same's commands, from the object clicked last); a
    genie or symbol: Select All Instances of its library element; a big filled rectangle or
    picture: Lock as Background."""
    if not ctx["indices"] or getattr(win, "doc", None) is None:
        return
    same = getattr(win, "select_same_actions", None)
    if same:
        sub = menu.addMenu("Select Same")
        sub.setIcon(same[0].icon())
        for a in same:
            sub.addAction(a)
    if len(ctx["units"]) != 1:
        return
    root = next(iter(ctx["units"]))
    key = win._element_key(win.doc.page.objects[root], keep_case=True)
    if key:
        n = len(win.instances_of(root))
        a = menu.addAction("Select All Instances of %s (%d on the page)" % (key, n))
        a.setIcon(win.act_select_same_element.icon())
        a.triggered.connect(lambda _=False, r=root: win.select_instances(r))
    if win._background_candidate() is not None:
        a = menu.addAction("Lock as Background")
        a.setIcon(win.act_lock.icon())
        a.setStatusTip("Lock it so a drag on it selects the objects on it with a box")
        a.triggered.connect(win._lock_as_background)


def _turn(win, menu, ctx) -> None:
    """Rotate and Flip: the Arrange menu's four commands."""
    if not ctx["indices"]:
        return
    sub = menu.addMenu("Rotate and Flip")
    sub.setIcon(win.act_rot_cw.icon())
    for a in (win.act_rot_cw, win.act_rot_ccw, win.act_flip_h, win.act_flip_v):
        sub.addAction(a)


register_section("guide", 3, _guide)
register_section("locked", 5, _locked)
register_section("stack", 8, _stack)
register_section("quick", 10, _quick)
register_section("object", 20, _object)
register_section("same", 25, _same)
register_section("arrange", 30, _arrange)
register_section("turn", 35, _turn)
register_section("clipboard", 40, _clipboard)
register_section("page", 90, _page)
