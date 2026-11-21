"""The ribbon's layout."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the page a window opens (the ribbon's layout does not depend on the page: any page gives
#: it a document window); the fake install's FxShapes (FxMain)
PAGE = "FxShapes"


def _pump(n=6):
    app = _app()
    for _ in range(n):
        app.processEvents()


def _window(fx_page=None, page=True):
    """A shown MainWindow; with ``fx_page`` (the fixture) and ``page`` it opens PAGE of the
    test's copy of the fake install."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    if page and fx_page is not None:
        w.open_document(fx_page(PAGE))
    w.ribbon._tabs.setCurrentIndex(0)
    _pump()
    return w


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()


def _resize(w, width, height=1000):
    w.resize(width, height)
    _pump()
    w.ribbon._reflow()
    return w.ribbon.layout_states()


# --------------------------------------------------------------------------- plan_layout
def test_plan_layout_is_a_pure_function_of_the_width():
    from ctgedit.ui.ribbon import plan_layout

    groups = [
        (2, {"full": 300, "compact": 200, "collapsed": 80}),  # Select
        (2, {"full": 300, "compact": 120, "collapsed": 80}),  # Place
        (1, {"full": 400, "compact": 250, "collapsed": 70}),  # Create
        (3, {"full": 200, "compact": 90, "collapsed": 60}),
    ]  # Data
    gap = 4
    full = 1200 + 3 * gap
    assert plan_layout(full, groups, gap) == ["full"] * 4
    # the highest priority number first, then right to left within a priority
    assert plan_layout(full - 1, groups, gap) == ["full", "full", "full", "compact"]
    assert plan_layout(full - 120, groups, gap) == ["full", "compact", "full", "compact"]
    # every group goes compact before any collapses (big buttons drop labels first)
    assert plan_layout(700, groups, gap) == ["compact"] * 4
    states = plan_layout(650, groups, gap)
    assert states == ["compact", "compact", "compact", "collapsed"]
    assert plan_layout(650, groups, gap) == states  # (no state kept)
    # a group collapses only when that makes it narrower
    odd = [(2, {"full": 100, "compact": 60, "collapsed": 90})]
    assert plan_layout(10, odd) == ["compact"]


# --------------------------------------------------------------------------- the window
def test_the_same_width_gives_the_same_layout_whatever_the_resize_path(fx_page):
    w = _window(fx_page)
    try:
        fresh = _resize(w, 1600)
        assert _resize(w, 1024) != fresh
        assert _resize(w, 1600) == fresh
        _resize(w, 2400)
        assert _resize(w, 1600) == fresh
        at_1280 = _resize(w, 1280, 720)
        _resize(w, 1366, 720)
        assert _resize(w, 1280, 720) == at_1280
    finally:
        _close(w)


def test_at_1600_no_design_group_is_collapsed_and_create_and_modify_keep_labels(fx_page):
    w = _window(fx_page)
    try:
        states = dict(_resize(w, 1600, 1000))
        assert "collapsed" not in states.values(), states
        assert states["Create"] == "full" and states["Modify"] == "full", states
    finally:
        _close(w)


def test_at_1280_the_select_and_symbol_tools_are_buttons(fx_page):
    w = _window(fx_page)
    try:
        for width in (1280, 1024, 800):
            _resize(w, width, 720)
            for tool in ("select", "symbol"):
                act = w.tool_actions[tool]
                btns = [
                    b
                    for b in w.ribbon.findChildren(QtWidgets.QToolButton)
                    if b.defaultAction() is act and b.isVisible()
                ]
                assert btns, (width, tool)
                # pinned: labelled even when its group folds
                assert btns[0].toolButtonStyle() == QtCore.Qt.ToolButtonTextUnderIcon
    finally:
        _close(w)


def test_place_never_folds_and_the_help_says_the_order_the_groups_fold_in(fx_page):
    """Place's folded width (its pinned Symbol tool + the fold button) is never narrower than
    its icons: it keeps them at every width, and the manual says so."""
    w = _window(fx_page)
    try:
        for width in (602, 700, 800, 1024, 1280):
            states = dict(_resize(w, width, 720))
            assert states["Place"] != "collapsed", (width, states)
        narrow = dict(_resize(w, 602, 720))
        assert narrow["Place"] == "compact", narrow
        assert all(narrow[c] == "collapsed" for c in ("Select", "Data")), narrow
    finally:
        _close(w)
    md = open(
        os.path.join(os.path.dirname(__file__), "..", "ctgedit", "help", "pages", "the-window.md"),
        encoding="utf-8",
    ).read()
    text = " ".join(md.split())
    assert "Data folds first, then Select, then Arrange, Modify and Create." in text
    assert "Place keeps its Symbol tool and shows its other commands as icons." in text
    assert "then Place and Select" not in text


def _hosted(groups):
    out = []
    for g in groups:
        out += [a for a in g.menu_actions() if a is not None]
    return out


def test_every_command_the_ribbon_hosts_has_an_icon():
    w = _window(page=False)
    try:
        rb = w.ribbon
        missing = []
        for title in rb.tab_titles():
            for a in _hosted(rb.groups_of(title)):
                if a.icon().isNull():
                    missing.append("%s: %s" % (title, a.text()))
        for b in rb.findChildren(QtWidgets.QToolButton):
            a = b.defaultAction()
            if a is not None and a.icon().isNull() and a is not w.act_collapse_ribbon:
                missing.append(a.text())
        assert not missing, missing
        # the Utilities tab shows icons
        util = rb.groups_of("Utilities")
        assert util and all(not a.icon().isNull() for a in _hosted(util))
    finally:
        _close(w)


def test_every_menu_command_has_an_icon_and_checkables_show_a_check():
    w = _window(page=False)
    try:
        # the menus filled at run time (recent pages, styles, labels, the window list)
        dynamic = {
            id(m) for m in (w._mru_menu, w._import_recent_menu, w.styles_menu, w.labels_menu, w._window_menu)
        }
        no_icon, checkable = [], []

        def walk(menu):
            if id(menu) in dynamic:
                return
            for a in menu.actions():
                if a.menu() is not None:
                    walk(a.menu())
                elif a.isSeparator():
                    continue
                elif a.isCheckable():
                    checkable.append(a)
                elif a.icon().isNull():
                    no_icon.append(a.text())

        for top in w.menuBar().actions():
            if top.menu() is not None:
                walk(top.menu())
        assert not no_icon, no_icon
        assert all(not a.isIconVisibleInMenu() for a in checkable)
    finally:
        _close(w)


def test_widths_are_measured_once_and_again_after_a_text_size_change(fx_page):
    from ctgedit.ui.style import theme

    app = _app()
    theme.apply_theme(app, "dark", 1.0)
    w = _window(fx_page)
    try:
        _resize(w, 1600)
        g = w.ribbon.groups_of("Design")[2]
        before = g.widths()
        assert g._widths == before  # cached
        theme.apply_theme(app, "dark", 1.5)
        _pump()
        assert g.widths()["full"] > before["full"]  # measured again, bigger
    finally:
        theme.apply_theme(app, "dark", 1.0)
        _close(w)


def test_a_small_growth_keeps_the_layout_a_shrink_recomputes(fx_page):
    w = _window(fx_page)
    try:
        rb = w.ribbon
        _resize(w, 1400)
        before = rb.layout_states()
        last = rb._last_layout
        _resize(w, 1410)  # less than 24 px wider
        assert rb._last_layout == last and rb.layout_states() == before
        _resize(w, 1000)
        assert rb._last_layout != last
    finally:
        _close(w)


def test_the_caption_menus_list_every_command_and_the_extras():
    w = _window(page=False)
    try:
        rb = w.ribbon
        by = {g.caption: g for g in rb.groups_of("Design")}
        menu = by["Modify"]._caption_btn.menu()
        menu.aboutToShow.emit()
        acts = [a for a in menu.actions() if not a.isSeparator()]
        assert w.act_rot_ccw in acts and w.act_flip_v in acts and w.act_ungroup in acts
        assert w.act_dup in acts and w.act_del in acts
        arrange = by["Arrange"].menu_actions()
        assert all(a in arrange for a in w.align_actions)
        assert w.act_fwd in arrange and w.act_bwd in arrange
        place = by["Place"].menu_actions()
        assert w.act_paste_symbol in place and w.act_paste_flash in place and w.act_paste_super in place
        assert by["Modify"]._caption_btn.text() == "Modify"
        assert by["Modify"]._caption_btn.popupMode() == QtWidgets.QToolButton.InstantPopup
    finally:
        _close(w)


def test_dividers_between_groups_and_none_after_the_last(fx_page):
    w = _window(fx_page)
    try:
        _resize(w, 1600)
        groups = [g for g in w.ribbon.groups_of("Design") if not g.hidden_by_user]
        assert [g.divider for g in groups] == [True] * (len(groups) - 1) + [False]
    finally:
        _close(w)


def test_rotate_and_flip_offer_both_directions_and_remember_the_last():
    from ctgedit.ui.ribbon import VariantButton
    from ctgedit.ui.prefs.settings import app_settings

    w = _window(page=False)
    try:
        vbs = {b._key: b for b in w.ribbon.findChildren(VariantButton)}
        rot, flip = vbs["ui/variant/rotate"], vbs["ui/variant/flip"]
        assert rot._actions == [w.act_rot_cw, w.act_rot_ccw]
        assert flip._actions == [w.act_flip_h, w.act_flip_v]
        rot._remember(w.act_rot_ccw)
        assert rot.defaultAction() is w.act_rot_ccw
        assert app_settings().value("ui/variant/rotate") == w.act_rot_ccw.text()
    finally:
        _close(w)


def test_a_big_split_button_fits_its_label_and_arrow():
    from ctgedit.ui.ribbon import VariantButton, _label_width

    w = _window(page=False)
    try:
        for b in w.ribbon.findChildren(VariantButton):
            if b.toolButtonStyle() != QtCore.Qt.ToolButtonTextUnderIcon:
                continue
            label = _label_width(b, [b.defaultAction().text()])
            assert b.minimumWidth() >= label + 14, b.defaultAction().text()
    finally:
        _close(w)


def test_a_folded_group_keeps_its_pinned_tool_and_shows_an_icon():
    _app()
    from ctgedit.ui.ribbon import RibbonGroup

    host = QtWidgets.QWidget()
    QtWidgets.QHBoxLayout(host)
    from ctgedit.ui.style.theme import icon

    a1, a2, a3 = (
        QtGui.QAction(icon(n), t, host) for n, t in (("select", "Pick"), ("goto", "Go"), ("find", "Look"))
    )
    g = RibbonGroup("Select", 2, host)
    host.layout().addWidget(g)
    pin = g.add_big(a1, pinned=True)
    big = g.add_big(a2)
    small = g.add_small(a3)
    host.show()
    g.set_collapsed(True)
    _pump()
    assert pin.isVisible() and not big.isVisible() and not small.isVisible()
    fold = g._collapsed_btn
    assert fold is not None and fold.isVisible()
    assert fold.icon().cacheKey() == a2.icon().cacheKey()
    assert fold.text() == "Select"
    assert a1 not in [a for a in g.menu_actions(with_pinned=False)]
    g.set_collapsed(False)
    _pump()
    assert big.isVisible() and small.isVisible() and g._collapsed_btn is None
    host.deleteLater()


def test_a_folded_group_whose_only_big_button_is_pinned_never_repeats_it():
    """The Select group: Select is pinned and its only big button. The fold button shows
    another command's icon, not a second Select arrow."""
    _app()
    from ctgedit.ui.ribbon import RibbonGroup
    from ctgedit.ui.style.theme import icon

    host = QtWidgets.QWidget()
    QtWidgets.QHBoxLayout(host)
    a1, a2, a3 = (
        QtGui.QAction(icon(n), t, host) for n, t in (("select", "Pick"), ("goto", "Go"), ("find", "Look"))
    )
    g = RibbonGroup("Select", 2, host)
    host.layout().addWidget(g)
    g.add_big(a1, pinned=True)
    g.add_small(a2)
    g.add_small(a3)
    host.show()
    g.set_collapsed(True)
    _pump()
    fold = g._collapsed_btn
    assert fold is not None and fold.isVisible()
    assert fold.icon().cacheKey() != a1.icon().cacheKey()
    assert fold.icon().cacheKey() == a2.icon().cacheKey()
    host.deleteLater()


def test_a_small_split_button_keeps_its_width_when_its_face_changes():
    """Rotate and Flip are small split buttons: a face change never gives them the big
    buttons' minimum width (the group would fold earlier than on a fresh start)."""
    _app()
    from ctgedit.ui.ribbon import RibbonGroup, VariantButton
    from ctgedit.ui.style.theme import icon

    host = QtWidgets.QWidget()
    QtWidgets.QHBoxLayout(host)
    a1, a2 = (
        QtGui.QAction(icon(n), t, host) for n, t in (("rotate_cw", "Turn right"), ("rotate_ccw", "Turn left"))
    )
    g = RibbonGroup("Modify", 2, host)
    host.layout().addWidget(g)
    vb = VariantButton([a1, a2], "test_small_split")
    g.add_small_widget(vb)
    host.show()
    g.set_compact(True)
    _pump()
    assert vb.small and vb.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly
    before = vb.minimumWidth()
    vb._remember(a2)
    _pump()
    assert vb.defaultAction() is a2
    assert vb.minimumWidth() == before == 0
    assert vb.minimumSizeHint().width() <= vb.sizeHint().width()
    g.set_compact(False)
    vb._remember(a1)
    _pump()
    assert vb.minimumWidth() == 0
    host.deleteLater()


def test_compact_density_hides_the_captions(monkeypatch):
    _app()
    from ctgedit.ui.style import theme
    from ctgedit.ui.ribbon import RibbonGroup

    host = QtWidgets.QWidget()
    QtWidgets.QHBoxLayout(host)
    g = RibbonGroup("Colours", 2, host)
    host.layout().addWidget(g)
    g.add_big(QtGui.QAction(theme.icon("swap_colours"), "Swap", host))
    host.show()
    _pump()
    assert g._caption_btn.isVisible()
    monkeypatch.setattr(theme, "density", lambda key: False if key == "captions" else None, raising=False)
    g.apply_metrics()
    _pump()
    assert not g._caption_btn.isVisible()
    host.deleteLater()
