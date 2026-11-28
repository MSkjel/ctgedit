"""Templates that sit mid-page still align."""

import os


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


def _template_index(objs, ref):
    return next(i for i, o in enumerate(objs) if ref in (o.raw.get("_ref", "") or ""))


def _mid_page_copy(fx_install):
    """FxTplFlash with its own root FxSteady moved in front of the template genie (the
    page's records are written by the test: the template is then mid-page)."""
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg

    p = parse_ctg(fx_install.page("FxTplFlash"))
    objs = p.page.objects
    assert objs[0].type_code == 0x08 and objs[-1].class_flag == 0  # template, own leaf
    objs[:] = [objs[-1]] + objs[:-1]
    out = os.path.join(fx_install.project("FxMain"), "FxTplMid.ctg")
    with open(out, "wb") as fh:
        fh.write(writer.serialize_ctg(p.container, p.page, p.attributes))
    return out


def test_offset_alignment_grafts_button_icons(fx_install):
    from ctgedit.editing import libraries_changed
    from ctgedit.io.reader import read_page

    path = _mid_page_copy(fx_install)
    pg = read_page(path)

    # the template genie is mid-page (its records are NOT a page-0 prefix)
    tgenie = _template_index(pg.objects, "fx_alt.flash@-XGA")
    assert tgenie > 0
    # alignment reaches past the page's own root: every record of the template's
    # subtree is a template copy (the template root and its five members), the page's
    # own rectangle is not
    sub = [i for i, o in enumerate(pg.objects) if i == tgenie or o.parent == tgenie]
    assert len(sub) == 6
    marked = [i for i, o in enumerate(pg.objects) if o.raw.get("_from_template")]
    assert marked == sub
    # the art-stripped bitmap symbol slot borrowed the template's embedded DIB
    icons = [
        i
        for i, o in enumerate(pg.objects)
        if o.kind.value == "symbol" and o.raw.get("_embedded_bmp") is not None
    ]
    assert len(icons) == 1 and icons[0] in sub

    # the picture comes from the .ctt: without the template library it is not there
    ctt = fx_install.file("FxStyle", "fx_alt.ctt")
    os.rename(ctt, ctt + ".away")
    libraries_changed()
    pg = read_page(path)
    assert not [o for o in pg.objects if o.raw.get("_embedded_bmp") is not None]


def test_locked_template_stacks_in_record_order_and_doubleclick_skips_it(fx_install):
    """A locked template stacks in record order and double-click skips it."""
    from PySide6 import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(fx_install.page("FxTplLast"))
        w.resize(1400, 1000)
        w.show()
        app.processEvents()
        # a root stored after the template (the page itself has none)
        w.doc.create_object("rectangle", (600, 500, 660, 540))
        w._refresh()
        app.processEvents()
        view, sc, pg = w.view, w.view.scene(), w.doc.page

        tgenie = _template_index(pg.objects, "fx_style.normal@XGA")
        assert tgenie > 0
        # stacking = record order: above every root stored before the
        # template, below every root stored after it
        order = {id(it): k for k, it in enumerate(sc.items(QtCore.Qt.AscendingOrder))}
        pos = order[id(sc._items[tgenie])]
        end = max(w.doc.subtree(tgenie))
        before = [i for i in sc._items if i < tgenie]
        after = [i for i in sc._items if i > end]
        assert before and after
        assert all(order[id(sc._items[i])] < pos for i in before)
        assert all(order[id(sc._items[i])] > pos for i in after)
        assert {sc._items[i].zValue() for i in sc._items} == {0.0}

        # the top-level content genie FxBeforeTpl, which sits under the template
        found = w.doc.find_objects_by_name("FxBeforeTpl")
        assert len(found) == 1
        content = found[0]
        o = pg.objects[content]
        assert o.is_genie and w.doc.root_of(content) == content
        assert not o.raw.get("_from_template") and content in sc._items
        g = o.geometry
        vp = view.mapFromScene(QtCore.QPointF(g.x + g.extent_x / 2, g.y + g.extent_y / 2))
        gpos = view.viewport().mapToGlobal(vp)
        ev = QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonDblClick,
            QtCore.QPointF(vp),
            QtCore.QPointF(gpos),
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
        )
        view.mouseDoubleClickEvent(ev)
        app.processEvents()
        assert ("genie", content) in w._dialogs
        assert ("genie", tgenie) not in w._dialogs  # NOT the template
    finally:
        for dlg in list(getattr(w, "_dialogs", {}).values()):
            dlg._ctg_dropped = True
            dlg.reject()
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()
        w.deleteLater()
        QtWidgets.QApplication.processEvents()
