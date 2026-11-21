"""Settings > Canvas."""

import os
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _wheel(view, dy, mods=None):
    from PySide6 import QtCore, QtGui

    pos = QtCore.QPointF(view.viewport().rect().center())
    ev = QtGui.QWheelEvent(
        pos,
        view.viewport().mapToGlobal(pos),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, dy),
        QtCore.Qt.NoButton,
        mods if mods is not None else QtCore.Qt.NoModifier,
        QtCore.Qt.NoScrollPhase,
        False,
    )
    view.wheelEvent(ev)


def test_the_wheel_zooms_or_scrolls():
    from PySide6 import QtCore, QtWidgets

    _app()
    from ctgedit.ui.editor.canvas import CanvasView

    scene = QtWidgets.QGraphicsScene(0, 0, 4000, 4000)
    view = CanvasView()
    view.setScene(scene)
    view.resize(400, 300)
    view.show()
    try:
        z0 = view.zoom()
        _wheel(view, -120)
        assert view.zoom() != z0  # (the default: the wheel zooms)
        view.WHEEL_SCROLLS = True
        z1, y1 = view.zoom(), view.verticalScrollBar().value()
        _wheel(view, -120)
        assert view.zoom() == z1 and view.verticalScrollBar().value() > y1
        _wheel(view, 120, QtCore.Qt.ControlModifier)
        assert view.zoom() != z1  # Ctrl + the wheel zooms
    finally:
        view.close()


def test_the_paste_offset_setting_is_clamped(monkeypatch, tmp_path):
    from ctgedit.ui.prefs import settings

    st = settings.app_settings()
    assert settings.paste_offset() == 10
    st.setValue("opt/paste_offset", 25)
    assert settings.paste_offset() == 25
    st.setValue("opt/paste_offset", 999)
    assert settings.paste_offset() == settings.PASTE_OFFSET_RANGE[1]
    st.setValue("opt/paste_offset", "x")
    assert settings.paste_offset() == 10


def test_duplicate_and_paste_use_the_offset():
    from ctgedit.ui.window.canvas_edits import CanvasEdits

    class Doc:
        def duplicate_objects(self, sel, offset=10):
            self.offset = offset
            return list(sel)

    class W:
        doc, _sel, _paste_offset = Doc(), [3], 0
        _filter_template = lambda self, s, what: s  # noqa: E731
        _refresh = _props_after_copy = lambda self, *a: None  # noqa: E731
        commit_nudge = lambda self: False  # noqa: E731
        # (no copy moved since a Duplicate: the paste offset, not a repeated one)
        _smart_offset = lambda self, sel: None  # noqa: E731
        _remember_duplicate = lambda self, *a, **k: None  # noqa: E731

    w = W()
    CanvasEdits.duplicate_selected(w)
    assert w.doc.offset == 0


def test_the_drawing_choices_reach_the_reader():
    from ctgedit.io import reader
    from ctgedit.ui.prefs import settings

    st = settings.app_settings()
    try:
        st.setValue("opt/stacked_labels", "all")
        st.setValue("opt/composite_genies", "cache")
        settings.apply_drawing_options()
        assert reader.get_render_option("stacked_labels") == "all"
        assert reader.get_composite_members() is False
        st.setValue("opt/stacked_labels", "nonsense")  # (an unknown value: the default)
        st.setValue("opt/composite_genies", "members")
        settings.apply_drawing_options()
        assert reader.get_render_option("stacked_labels") == "top"
        assert reader.get_composite_members() is True
    finally:
        reader.set_render_option("stacked_labels", "top")
        reader.set_composite_members(True)


def test_every_copy_of_a_stacked_label_with_the_graphics_builder_choice():
    from ctgedit.io import reader
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page, Visibility

    def page():
        p = Page(name="stack")
        for an in (1, 2):
            o = GraphicObject(
                kind=ObjectKind.TEXT, an=an, geometry=Geometry(x=10, y=10, extent_x=40, extent_y=12)
            )
            o.style.text = "Login"
            o.animations.visibility = Visibility(expression="X%d" % an)
            p.objects.append(o)
        return p

    p = page()
    reader._suppress_overlapping_duplicates(p)
    assert p.objects[0].raw.get("_dup_suppressed")
    try:
        reader.set_render_option("stacked_labels", "all")
        p = page()
        reader._suppress_overlapping_duplicates(p)
        assert not any(o.raw.get("_dup_suppressed") for o in p.objects)
    finally:
        reader.set_render_option("stacked_labels", "top")


def test_the_value_placeholder_in_the_runtime_look(fx_page):
    """FxTextKinds (the fake install) holds FxStrLeftover, a string display whose shown
    text comes from code: the placeholder replaces it, as it does every such text."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import reader

    def texts(holder):
        with reader.derive_options(label_mode="native", value_placeholder=holder):
            doc = EditableDocument.open(fx_page("FxTextKinds"))
        return doc, [o.style.text for o in doc.page.objects]

    doc, with_abc = texts("abc")
    _doc, blank = texts("")
    changed = [k for k, (a, b) in enumerate(zip(with_abc, blank)) if a != b]
    assert changed
    assert all(with_abc[k] == "abc" and blank[k] == "" for k in changed)
    assert doc.find_objects_by_name("FxStrLeftover")[0] in changed


def test_a_page_opens_at_100_percent_when_asked():
    from PySide6 import QtWidgets

    _app()
    from ctgedit.ui.editor.canvas import CanvasView
    from ctgedit.ui.prefs import settings

    class Scene(QtWidgets.QGraphicsScene):
        def page_rect(self):
            from PySide6 import QtCore

            return QtCore.QRectF(0, 0, 3000, 2000)

    view = CanvasView()
    sc = Scene(-100, -100, 3200, 2200)
    view.setScene(sc)
    view.resize(500, 400)
    view.show()
    try:
        view.fit()
        assert abs(view.zoom() - 1.0) > 0.05
        view.open_actual_size()
        QtWidgets.QApplication.processEvents()
        assert abs(view.zoom() - 1.0) < 1e-9
        # (a page bigger than the view: its top-left corner shows)
        tl = view.mapToScene(view.viewport().rect().topLeft())
        assert -20 <= tl.x() <= 5 and -20 <= tl.y() <= 5, tl
    finally:
        view.close()
    assert (
        dict(settings.OPEN_ZOOM)["fit"]
        and settings.choice("opt/open_zoom", settings.OPEN_ZOOM, "fit") == "fit"
    )


def test_the_undo_memory_setting():
    from ctgedit import editing
    from ctgedit.ui.prefs import settings

    st = settings.app_settings()
    before = editing.UNDO_BYTES_TOTAL
    try:
        assert settings.undo_mb() == 384
        st.setValue("opt/undo_mb", 1024)
        settings.apply_undo_budget()
        assert editing.UNDO_BYTES_TOTAL == 1024 * 1024 * 1024
        st.setValue("opt/undo_mb", 1)
        assert settings.undo_mb() == settings.UNDO_MB_RANGE[0]
    finally:
        editing.UNDO_BYTES_TOTAL = before


def test_extra_library_folders_join_the_ones_the_process_started_with(monkeypatch, tmp_path):
    from ctgedit.ui.prefs import settings

    monkeypatch.setattr(settings, "_LIB_PATH_AT_START", r"C:\Deploy\Libs")
    monkeypatch.setenv("CTG_LIB_PATH", r"C:\Deploy\Libs")
    st = settings.app_settings()
    st.setValue("opt/library_folders", r" D:\Shared\Libs ; ;E:\More ")
    assert settings.library_folders() == [r"D:\Shared\Libs", r"E:\More"]
    assert settings.apply_library_folders() is True
    assert os.environ["CTG_LIB_PATH"] == os.pathsep.join([r"C:\Deploy\Libs", r"D:\Shared\Libs", r"E:\More"])
    assert settings.apply_library_folders() is False  # (nothing changed)
    st.setValue("opt/library_folders", "")
    assert settings.apply_library_folders() is True
    assert os.environ["CTG_LIB_PATH"] == r"C:\Deploy\Libs"


def test_the_code_page_setting_applies_at_start_unless_the_environment_names_one(monkeypatch):
    from ctgedit.ui.prefs import settings

    st = settings.app_settings()
    monkeypatch.delenv("CTG_CODEPAGE", raising=False)
    settings.apply_codepage_at_start()
    assert "CTG_CODEPAGE" not in os.environ  # ('auto': as Windows)
    st.setValue("opt/codepage", "1250")
    settings.apply_codepage_at_start()
    assert os.environ["CTG_CODEPAGE"] == "1250"
    monkeypatch.setenv("CTG_CODEPAGE", "1251")
    settings.apply_codepage_at_start()
    assert os.environ["CTG_CODEPAGE"] == "1251"  # (a deployment's choice wins)
    st.setValue("opt/codepage", "nonsense")
    monkeypatch.delenv("CTG_CODEPAGE")
    settings.apply_codepage_at_start()
    assert "CTG_CODEPAGE" not in os.environ
