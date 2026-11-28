"""Shared picker widgets, one per concept."""

from __future__ import annotations

from ..infra.logsetup import note_exception as _note_exc

from ..infra.paths import lib_path_dirs
from .style.theme import sp as _sp

import os
from typing import Callable, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from .messages import warn

#: the text an empty page / area field shows (CtDraw's)
NONE_TEXT = "<None>"

# Citect template resolution decorations -> the labels CtDraw shows (help
# 1172104), in its order; 'User' = a template sized by its View area
_RESOLUTION_ORDER = ("VGA", "SVGA", "XGA", "SXGA", "HD768", "HD720", "HD1080", "WUXGA", "UHD4K", "4K", "User")
_RESOLUTION_LABELS = {
    "VGA": "VGA (640x480, 4:3)",
    "SVGA": "SVGA (800x600, 4:3)",
    "XGA": "XGA (1024x768, 4:3)",
    "SXGA": "SXGA (1280x1024, 5:4)",
    "HD768": "HD768 (1366x768, 16:9)",
    "HD720": "HD720 (1280x720, 16:9)",
    "HD1080": "HD1080 (1920x1080, 16:9)",
    "WUXGA": "WUXGA (1920x1200, 16:10)",
    "UHD4K": "UHD4K (3840x2160, 16:9)",
    "4K": "UHD4K (3840x2160, 16:9)",
    "User": "User Defined",
}


def library_stem(path: str) -> str:
    """'C:\\...\\sxw_style_1.ctt' -> 'sxw_style_1'."""
    return os.path.splitext(os.path.basename(path or ""))[0]


def preview_size() -> QtCore.QSize:
    from .browser import thumbs

    return QtCore.QSize(thumbs.PREVIEW_SIZE)


def _card_size() -> QtCore.QSize:
    from .browser import thumbs

    return QtCore.QSize(thumbs.CARD_SIZE)


# ------------------------------------------------------------------ the catalog
#: project dir (and CTG_LIB_PATH) -> (the template libraries' stamps, TemplateCatalog)
_TEMPLATE_CATALOGS: Dict[tuple, tuple] = {}


def _search_dirs(project_dir: str) -> List[str]:
    """The project's folder, CTG_LIB_PATH and their include chains, in search order (no
    folder: only CTG_LIB_PATH, never the current directory)."""
    from ..format.projects import include_search_dirs

    extra = lib_path_dirs()
    dirs: List[str] = []
    for d in ([project_dir] if project_dir else []) + extra:
        if d not in dirs:
            dirs.append(d)
        try:
            for p in include_search_dirs(d):
                if p not in dirs:
                    dirs.append(p)
        except Exception:  # noqa: BLE001 -- swallow-ok: a broken include chain lists less
            continue
    return dirs


def _template_libraries(dirs: List[str]) -> List[Tuple[str, int, int]]:
    """(path, mtime, size) of every template library (.ctt) in ``dirs``, in search order."""
    out = []
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if not fn.lower().endswith(".ctt"):
                continue
            path = os.path.join(d, fn)
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append((path, st.st_mtime_ns, st.st_size))
    return out


class TemplateCatalog:
    """The page templates a project can use."""

    def __init__(self, project_dir: str, libraries: Optional[List[tuple]] = None):
        from ..format import pagesummary

        self.project_dir = os.path.abspath(project_dir) if project_dir else ""
        if libraries is None:
            libraries = _template_libraries(_search_dirs(self.project_dir))
        #: what the catalog was read from (for_project compares it)
        self.libraries = list(libraries)
        self._styles: dict = {}  # style lower -> [display name, [(lib path, summary)]]
        for path, _mtime, _size in self.libraries:
            stem = os.path.splitext(os.path.basename(path))[0]
            ent = self._styles.setdefault(stem.lower(), [stem, []])
            have = {s.name.lower() for _p, s in ent[1]}
            for s in pagesummary.read_element_summaries(path, want_thumbnail=False):
                if s.ok and s.name.lower() not in have:
                    have.add(s.name.lower())
                    s.thumb_pending = True  # (read when first drawn: thumbnail_pixmap)
                    ent[1].append((path, s))
        # an empty library (Example\Standard.ctt) is no style of its own
        self._styles = {k: v for k, v in self._styles.items() if v[1]}

    @classmethod
    def for_project(cls, project_dir: str) -> "TemplateCatalog":
        """The project's catalog, kept while its template libraries are the same files
        (the same paths, times and sizes)."""
        pdir = os.path.abspath(project_dir) if project_dir else ""
        key = (os.path.normcase(pdir), os.environ.get("CTG_LIB_PATH", ""))
        libraries = _template_libraries(_search_dirs(pdir))
        hit = _TEMPLATE_CATALOGS.get(key)
        if hit is not None and hit[0] == libraries:
            return hit[1]
        cat = cls(pdir, libraries)
        _TEMPLATE_CATALOGS[key] = (libraries, cat)
        return cat

    def styles(self) -> list:
        return sorted((v[0] for v in self._styles.values()), key=str.lower)

    def has_style(self, style: str) -> bool:
        return (style or "").lower() in self._styles

    def style_name(self, style: str) -> str:
        ent = self._styles.get((style or "").lower())
        return ent[0] if ent else style

    def elements(self, style: str) -> list:
        """[(library path, ElementSummary)] of ``style``, in library order."""
        ent = self._styles.get((style or "").lower())
        return list(ent[1]) if ent else []

    def find(self, style: str, element: str):
        for path, s in self.elements(style):
            if s.name.lower() == (element or "").lower():
                return path, s
        return None

    def resolutions(self, style: str) -> list:
        """The resolutions of ``style``'s templates, in CtDraw's order."""
        from ..format.template_place import split_element

        out = []
        for _p, s in self.elements(style):
            r = split_element(s.name)[1]
            if r and r not in out:
                out.append(r)
        order = {r.lower(): i for i, r in enumerate(_RESOLUTION_ORDER)}
        return sorted(out, key=lambda r: (order.get(r.lower(), 99), r.lower()))


def template_thumbnail(summary) -> bytes:
    """The template's thumbnail (BMP bytes; b'' for none), read from its library when
    first asked for (a TemplateCatalog lists its templates without them)."""
    if summary is None:
        return b""
    if getattr(summary, "thumb_pending", False):
        from ..format import pagesummary

        summary.thumb_pending = False
        full = pagesummary.read_element_summary(summary.library_path, summary.name)
        if full is not None and full.offset == summary.offset:
            summary.thumb_bmp = full.thumb_bmp
    return summary.thumb_bmp


def thumbnail_pixmap(summary, box: QtCore.QSize, background=None) -> QtGui.QPixmap:
    """A template's embedded thumbnail fitted into ``box`` over the page
    colour (an empty frame when the element has none)."""
    from .style import theme
    from .browser.thumbs import bmp_to_qimage, fit_image

    pm = QtGui.QPixmap(box)
    fill = theme.color("panel")
    if background is not None and (int(background) >> 24) & 0xFF != 0xFF:
        b = int(background)
        fill = QtGui.QColor((b >> 16) & 0xFF, (b >> 8) & 0xFF, b & 0xFF)
    pm.fill(fill)
    img = bmp_to_qimage(template_thumbnail(summary)) if summary is not None else QtGui.QImage()
    if not img.isNull():
        img = fit_image(img, box)
        p = QtGui.QPainter(pm)
        p.drawImage((box.width() - img.width()) // 2, (box.height() - img.height()) // 2, img)
        p.end()
    return pm


def element_thumbnail(
    lib_path: str, element: str, size: QtCore.QSize = QtCore.QSize(64, 64)
) -> Optional[QtGui.QPixmap]:
    """A library element's embedded thumbnail scaled into ``size`` (None
    when it has none or the file cannot be read)."""
    if not lib_path or not element:
        return None
    try:
        from ..format.pagesummary import read_element_summary

        s = read_element_summary(lib_path, element)
        rgba = s.thumbnail_rgba() if s is not None else None
    except Exception:  # noqa: BLE001 -- no thumbnail
        _note_exc("pick_fields.element_thumbnail")
        rgba = None
    if not rgba:
        return None
    w, h, data = rgba
    img = QtGui.QImage(data, w, h, 4 * w, QtGui.QImage.Format_RGBA8888).copy()
    pm = QtGui.QPixmap.fromImage(img)
    if pm.width() > size.width() or pm.height() > size.height():
        pm = pm.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    return pm


# ------------------------------------------------------------------ TemplatePicker
class TemplatePicker(QtWidgets.QWidget):
    """Choose a page template: Style, Resolution, the style's templates as
    thumbnails, 'Designed for showing title bar' and a preview.
    :meth:`value` = (library path, element) or None; ``changed`` on every
    change of the choice, ``activated`` on a double-click."""

    changed = QtCore.Signal()
    activated = QtCore.Signal()

    ALL = "All resolutions"
    _MISSING = "__missing__"

    def card_width(self) -> int:
        return self._card_w

    def set_card_width(self, px, remember: bool = True) -> None:
        """The cards ``px`` wide (None or garbled: the thumbnail size), the icon and grid
        sizes with it, within the page browser's card span (browser/delegates.card_size: the
        one span of every card grid); ``remember`` keeps it for the next picker."""
        from .browser.delegates import card_size

        base = _card_size()
        try:
            w = int(px) if px is not None else base.width()
        except TypeError, ValueError:  # swallow-ok: a garbled setting reads as the default
            w = base.width()
        w = card_size(w).width()
        h = max(1, int(round(w * base.height() / max(1, base.width()))))
        self._card_w = w
        self.list.setIconSize(QtCore.QSize(w, h))
        self.list.setGridSize(QtCore.QSize(w + 16, h + 40))
        if remember:
            from .prefs.settings import app_settings

            app_settings().setValue("pagebrowser/template/card_size", w)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if ev.type() == QtCore.QEvent.Wheel and obj is self.list.viewport():
            from .browser.thumbs import card_wheel_delta

            d = card_wheel_delta(ev)
            if d is not None:
                if d:
                    self.set_card_width(self._card_w + d)
                ev.accept()
                return True
        return super().eventFilter(obj, ev)

    def __init__(
        self,
        project_dir: str,
        parent=None,
        *,
        catalog: Optional[TemplateCatalog] = None,
        background=None,
        style: str = "",
        resolution: str = "",
        title_bar: bool = False,
    ):
        super().__init__(parent)
        self._catalog = catalog or TemplateCatalog.for_project(project_dir)
        self._background = background
        self._missing: Optional[Tuple[str, str]] = None  # (style, element) not found
        self._variants: Dict[tuple, dict] = {}
        self._want_res = resolution or ""
        self._syncing = False
        from .style import theme

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QtWidgets.QFormLayout()
        self.cb_style = QtWidgets.QComboBox()
        self.cb_style.setEditable(False)
        for s in self._catalog.styles():
            self.cb_style.addItem(s, s)
        self.cb_res = QtWidgets.QComboBox()
        self.cb_res.setEditable(False)
        top.addRow("Style:", self.cb_style)
        top.addRow("Resolution:", self.cb_res)
        lay.addLayout(top)

        mid = QtWidgets.QHBoxLayout()
        card = _card_size()
        self.list = QtWidgets.QListWidget()
        self.list.setViewMode(QtWidgets.QListView.IconMode)
        self.list.setResizeMode(QtWidgets.QListView.Adjust)
        self.list.setMovement(QtWidgets.QListView.Static)
        self.list.setWordWrap(True)
        self.list.setMinimumWidth(card.width() * 2 + 48)
        self.list.setAccessibleName("Templates")
        # the card size: Ctrl + the wheel over the cards, as in the Library panel and Open,
        # remembered
        self._card_w = card.width()
        from .prefs.settings import app_settings

        self.set_card_width(app_settings().value("pagebrowser/template/card_size", None), remember=False)
        self.list.viewport().installEventFilter(self)
        mid.addWidget(self.list, 1)
        side = QtWidgets.QVBoxLayout()
        self.preview = QtWidgets.QLabel()
        self.preview.setFixedSize(preview_size())
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setFrameShape(QtWidgets.QFrame.StyledPanel)
        side.addWidget(self.preview)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        side.addWidget(self.info)
        self.chk_titlebar = QtWidgets.QCheckBox("Designed for showing title bar")
        self.chk_titlebar.setChecked(bool(title_bar))
        self.chk_titlebar.setToolTip("Use the form of the template made for a window with a title bar")
        side.addWidget(self.chk_titlebar)
        self.side = side
        side.addStretch(1)
        mid.addLayout(side, 0)
        lay.addLayout(mid, 1)
        self.note = theme.note_label("", "warn")
        self.note.hide()
        lay.addWidget(self.note)

        self.cb_style.currentIndexChanged.connect(lambda _i: self._style_changed())
        self.cb_res.currentIndexChanged.connect(lambda _i: self._res_changed())
        self.list.currentItemChanged.connect(lambda *_a: self._selected())
        self.list.itemDoubleClicked.connect(lambda _it: self._double_clicked())
        self.chk_titlebar.toggled.connect(lambda _b: self._title_bar_toggled())

        want = self._catalog.style_name(style) if style else ""
        i = self.cb_style.findData(want, QtCore.Qt.UserRole, QtCore.Qt.MatchFixedString) if want else -1
        self.cb_style.blockSignals(True)
        self.cb_style.setCurrentIndex(i if i >= 0 else 0)
        self.cb_style.blockSignals(False)
        self._style_changed()
        if not self._catalog.styles():
            self._show_note(
                "No page templates are on this project's include path. "
                "Include a style project (for example SxW_Style_Include) first."
            )

    # -- the catalog ----------------------------------------------------------------
    @property
    def catalog(self) -> TemplateCatalog:
        return self._catalog

    def set_background(self, background) -> None:
        """Show the thumbnails over another page colour."""
        self._background = background
        keep = self.current_key()
        self._fill(select=keep)

    # -- population -----------------------------------------------------------------
    def _show_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def _style_is_missing(self) -> bool:
        return self.cb_style.currentData() == self._MISSING

    def _style_changed(self, fill: bool = True) -> None:
        self.cb_res.blockSignals(True)
        self.cb_res.clear()
        self.cb_res.addItem(self.ALL, "")
        if not self._style_is_missing():
            for r in self._catalog.resolutions(self.style_name()):
                self.cb_res.addItem(_RESOLUTION_LABELS.get(r, r), r)
        want = self._want_res or ""
        j = self.cb_res.findData(want, QtCore.Qt.UserRole, QtCore.Qt.MatchFixedString) if want else -1
        if j < 0 and want.upper() in ("UHD4K", "4K"):
            j = max(self.cb_res.findData("4K"), self.cb_res.findData("UHD4K"))
        self.cb_res.setCurrentIndex(j if j >= 0 else 0)
        self.cb_res.blockSignals(False)
        if fill:
            self._fill()

    def _res_changed(self) -> None:
        if not self._syncing:
            self._want_res = self.cb_res.currentData() or ""
        self._fill()

    def _pairs(self) -> dict:
        """{(name, res): {title_bar: (lib path, summary)}} of the style,
        filtered by the resolution."""
        from ..format.template_place import split_element

        res = self.cb_res.currentData() or ""
        out: dict = {}
        if self._style_is_missing():
            return out
        for path, s in self._catalog.elements(self.style_name()):
            name, r, tb = split_element(s.name)
            if res and r.lower() != res.lower():
                continue
            out.setdefault((name, r), {})[tb] = (path, s)
        return out

    def _fill(self, select=None) -> None:
        if select is None:
            select = self.current_key()  # keep the choice when it is still listed
        self.list.blockSignals(True)
        self.list.clear()
        self._variants = self._pairs()
        card = _card_size()
        first = prefer = chosen = None
        tb_now = self.chk_titlebar.isChecked()
        for (name, r), var in sorted(
            self._variants.items(), key=lambda kv: (kv[0][0].lower(), kv[0][1].lower())
        ):
            path, s = var.get(tb_now) or next(iter(var.values()))
            label = name if not r else "%s\n%s" % (name, r)
            it = QtWidgets.QListWidgetItem(QtGui.QIcon(thumbnail_pixmap(s, card, self._background)), label)
            it.setData(QtCore.Qt.UserRole, (name, r))
            it.setToolTip("\n".join("%s.%s" % (self.style_name(), v[1].name) for v in var.values()))
            self.list.addItem(it)
            if first is None:
                first = it
            if prefer is None and name.lower() in ("normal", "pagecontent"):
                prefer = it
            if isinstance(select, tuple) and (name.lower(), r.lower()) == (
                str(select[0]).lower(),
                str(select[1]).lower(),
            ):
                chosen = it
        miss = self._missing
        if miss is not None and (self._style_is_missing() or miss[0].lower() == self.style_name().lower()):
            it = QtWidgets.QListWidgetItem(
                QtGui.QIcon(thumbnail_pixmap(None, card, self._background)), "%s.%s (not found)" % miss
            )
            it.setData(QtCore.Qt.UserRole, self._MISSING)
            it.setToolTip("This template is not on this project's include path")
            it.setForeground(QtGui.QBrush(QtGui.QColor(_dim())))
            self.list.insertItem(0, it)
            if select == self._MISSING:
                chosen = it
        pick = chosen or prefer or first
        if pick is not None:
            self.list.setCurrentItem(pick)
        self.list.blockSignals(False)
        if self.list.count() == 0 and self._catalog.styles():
            self._show_note("Style %s has no templates for this resolution." % self.style_name())
        elif self._catalog.styles():
            self._show_note("")
        self._selected()

    # -- selection ------------------------------------------------------------------
    def current_key(self):
        it = self.list.currentItem()
        return it.data(QtCore.Qt.UserRole) if it is not None else None

    def _variant_map(self) -> Optional[dict]:
        key = self.current_key()
        if key is None or key == self._MISSING:
            return None
        return self._variants.get(key)

    def _choice(self):
        """(lib path, ElementSummary) of the selected template variant."""
        var = self._variant_map()
        if not var:
            return None
        return var.get(self.chk_titlebar.isChecked()) or next(iter(var.values()))

    def _title_bar_toggled(self) -> None:
        if self._syncing:
            return
        self._selected()

    def _double_clicked(self) -> None:
        if self._choice() is not None:
            self.activated.emit()

    def _selected(self) -> None:
        var = self._variant_map()
        missing = self.current_key() == self._MISSING
        # the title-bar choice only matters when the template exists in both forms
        both = bool(var) and len(var) == 2
        self.chk_titlebar.setEnabled(both or (not var and not missing))
        if var and not both:
            only = next(iter(var))
            if self.chk_titlebar.isChecked() != only:
                self._syncing = True
                try:
                    self.chk_titlebar.setChecked(only)
                finally:
                    self._syncing = False
        ch = self._choice()
        size = self.preview.size()
        if ch is None:
            self.preview.setPixmap(thumbnail_pixmap(None, size, self._background))
            self.preview.setToolTip("")
            self.info.setText("")
        else:
            path, s = ch
            self.preview.setPixmap(thumbnail_pixmap(s, size, self._background))
            self.preview.setToolTip("%s.%s" % (self.style_name(), s.name))
            from ..format.template_place import split_element

            _n, r, tb = split_element(s.name)
            parts = []
            if r:
                parts.append(r)
            parts.append("title bar" if tb else "no title bar")
            parts.append("project %s" % os.path.basename(os.path.dirname(path)))
            self.info.setText(", ".join(parts))
            self.info.setToolTip(path)
        self.changed.emit()

    # -- values ---------------------------------------------------------------------
    def style_name(self) -> str:
        """The style (template library) shown."""
        if self._style_is_missing():
            return self._missing[0] if self._missing else ""
        return str(self.cb_style.currentData() or self.cb_style.currentText())

    def resolution(self) -> str:
        """The resolution filter ('' = all resolutions)."""
        return str(self.cb_res.currentData() or "")

    def title_bar(self) -> bool:
        return self.chk_titlebar.isChecked()

    def current_name(self) -> str:
        """The name of the selected template (without its resolution)."""
        key = self.current_key()
        if key == self._MISSING and self._missing:
            from ..format.template_place import split_element

            return split_element(self._missing[1])[0]
        return str(key[0]) if key else ""

    def value(self) -> Optional[Tuple[str, str]]:
        """(library path, element name) of the chosen template, or None."""
        ch = self._choice()
        return (ch[0], ch[1].name) if ch else None

    def summary(self):
        ch = self._choice()
        return ch[1] if ch else None

    def element_name(self) -> str:
        ch = self._choice()
        return ch[1].name if ch else ""

    def template_ref(self) -> str:
        """'<style>.<element>.<ctt' of the chosen template, or ''."""
        v = self.value()
        return "%s.%s.<ctt" % (library_stem(v[0]), v[1]) if v else ""

    def is_missing(self) -> bool:
        """The shown template is one that was not found."""
        return self.current_key() == self._MISSING

    def set_resolution(self, res: str) -> None:
        self._want_res = res or ""
        j = self.cb_res.findData(self._want_res, QtCore.Qt.UserRole, QtCore.Qt.MatchFixedString)
        self.cb_res.setCurrentIndex(j if j >= 0 else 0)

    def set_current(self, style: str, element: str) -> None:
        """Preselect template ``style``.``element``; one that is not found
        stays in the list as '<style>.<element> (not found)'."""
        from ..format.template_place import split_element

        name, res, tb = split_element(element or "")
        found = self._catalog.find(style, element) if style and element else None
        self._missing = None if found is not None or not (style and element) else (style, element)
        self.cb_style.blockSignals(True)
        self._syncing = True
        try:
            self.chk_titlebar.setChecked(bool(tb))
            self._want_res = res
            m = self.cb_style.findData(self._MISSING)
            if m >= 0:
                self.cb_style.removeItem(m)
            k = (
                self.cb_style.findData(
                    self._catalog.style_name(style), QtCore.Qt.UserRole, QtCore.Qt.MatchFixedString
                )
                if style
                else -1
            )
            if k < 0 and self._missing is not None:
                self.cb_style.addItem("%s (not found)" % style, self._MISSING)
                k = self.cb_style.count() - 1
            if k >= 0:
                self.cb_style.setCurrentIndex(k)
        finally:
            self._syncing = False
            self.cb_style.blockSignals(False)
        self._style_changed(fill=False)
        self._fill(select=self._MISSING if self._missing else (name, res))


def _dim() -> str:
    from .style import theme

    return theme.COLORS.get("text_dim", theme.DARK["text_dim"])


# ------------------------------------------------------------------ PageField
def _page_names(project_dir: Optional[str]) -> List[Tuple[str, str]]:
    """(page name, project) of the pages ``project_dir`` can reach."""
    if not project_dir:
        return []
    try:
        from ..io import project as P

        rows = P.chain_page_names(project_dir)
    except Exception:  # noqa: BLE001 -- the list is an aid
        _note_exc("pick_fields._page_names")
        return []
    out = []
    for r in rows or ():
        if isinstance(r, (tuple, list)):
            name = str(r[0]) if r else ""
            proj = str(r[1]) if len(r) > 1 and r[1] else ""
        else:
            name = str(getattr(r, "name", r))
            proj = str(getattr(r, "project", "") or "")
        if name:
            out.append((name, proj))
    return out


#: the widest a drop-down list opens (px): a longer name scrolls sideways in it
POPUP_MAX_WIDTH = 640


def widen_popup(combo: QtWidgets.QComboBox, most: int = POPUP_MAX_WIDTH) -> None:
    """Before ``combo.showPopup``: its list opens as wide as its longest item (at least the
    box, at most ``most``). The app style opens an editable combo's list as wide as the box
    only, which cuts long names."""
    view = combo.view()
    want = view.sizeHintForColumn(0) + view.verticalScrollBar().sizeHint().width() + 8
    view.setMinimumWidth(max(combo.width(), min(want, most)))


def project_choices(current_path: str = ""):
    """(name, folder) of the active install's projects, and the name of ``current_path``'s
    project (or '')."""
    try:
        from ..io.catalog import catalog_for

        cat = catalog_for()
        projects = [(p.name, p.dir) for p in cat.projects() if p.exists and not p.is_folder]
        here = cat.project_for_path(current_path) if current_path else None
        return projects, (here.name if here is not None else "")
    except Exception:  # noqa: BLE001 -- swallow-ok: no install, no project list
        return [], ""


def project_dirs(name: str, includes: bool) -> list:
    """The folders 'All pages of the project' covers: the project, and with ``includes``
    the projects it includes (not AVEVA's own Include and System)."""
    try:
        from ..io.catalog import catalog_for

        cat = catalog_for()
        p = cat.project(name)
        if p is None:
            return []
        if not includes:
            return [p.dir]
        return [x.dir for x in cat.closure(p) if not x.is_implicit and x.exists]
    except Exception:  # noqa: BLE001 -- swallow-ok: an unknown project covers nothing
        return []


class ProjectScopeField(QtWidgets.QWidget):
    """The project picker of the dialogs that work on whole projects, with 'And the projects
    it includes'. ``remember``: the dialog's name its choice is kept under (scope/<name>/);
    a remembered project is chosen, unless ``fixed``: then ``current_path``'s project (the
    page or project the dialog was opened for). Else the open page's project, else the one
    Update Pages ran on last. A folder the install does not list is added by name."""

    changed = QtCore.Signal()

    INCLUDES_TEXT = "And the projects it includes"

    def __init__(
        self,
        current_path: str = "",
        parent=None,
        *,
        what: str = "The project whose pages are searched",
        includes_what: str = "the pages",
        horizontal: bool = False,
        remember: str = "",
        fixed: bool = False,
    ):
        super().__init__(parent)
        from .prefs.settings import app_settings

        lay = QtWidgets.QHBoxLayout(self) if horizontal else QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QtWidgets.QComboBox()
        self.combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.combo.setToolTip(what)
        projects, self.here = project_choices(current_path or "")
        for name, folder in projects:
            self.combo.addItem(name, folder)
        folder = os.path.dirname(os.path.abspath(current_path)) if current_path else ""
        if folder and not self.here and os.path.isdir(folder):
            self.here = os.path.basename(os.path.normpath(folder))
            self.combo.insertItem(0, self.here, folder)
        self.includes = QtWidgets.QCheckBox(self.INCLUDES_TEXT)
        self.includes.setToolTip(
            "Also %s of the projects it includes (not AVEVA's own Include and System projects)"
            % includes_what
        )
        self._key = "scope/%s/" % remember if remember else ""
        st = app_settings()
        kept = str(st.value(self._key + "project", "") or "") if self._key else ""
        if not ((not fixed and self.set_project(kept)) or self.set_project(self.here)):
            self.set_project(str(st.value("updatepages/root", "") or ""))
        if self._key and not fixed:
            self.includes.setChecked(st.value(self._key + "includes", False, type=bool))
        for w in (self.combo, self.includes):
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0 if horizontal else _sp(4), 0, 0, 0)
            row.addWidget(w, 1 if w is self.combo else 0)
            if w is self.includes:
                row.addStretch(1)
            lay.addLayout(row)
        self.combo.currentTextChanged.connect(lambda _t: self._changed())
        self.includes.toggled.connect(lambda _on: self._changed())

    def _changed(self) -> None:
        if self._key:
            from .prefs.settings import app_settings

            st = app_settings()
            st.setValue(self._key + "project", self.project())
            st.setValue(self._key + "includes", self.includes.isChecked())
        self.changed.emit()

    def has_projects(self) -> bool:
        return self.combo.count() > 0

    def project(self) -> str:
        return self.combo.currentText()

    def set_project(self, name: str) -> bool:
        """Choose project ``name`` when it is listed; False when it is not."""
        if name and self.combo.findText(name) >= 0:
            self.combo.setCurrentText(name)
            return True
        return False

    def project_dir(self) -> str:
        """The chosen project's folder ('' = none)."""
        return str(self.combo.currentData() or "")

    def project_dirs(self) -> list:
        """The chosen project's folder, and with 'And the projects it includes' theirs (a
        folder the install does not list: itself)."""
        listed = project_dirs(self.project(), self.includes.isChecked())
        own = self.project_dir()
        return listed or ([own] if own else [])


def genie_entries(path: str) -> list:
    """The catalog entries (io.catalog.Entry) of every genie the project of ``path`` (a page
    or a library file) can place: its include chain's genie libraries, Include and System
    too, without super genies and without a copy another library of the chain shadows.
    Directory reads only. [] outside the install's projects."""
    try:
        from ..io.catalog import catalog_for, is_super_genie

        cat = catalog_for()
        info = cat.project_for_path(path) if path else None
        if info is None and path:
            info = cat.folder(os.path.dirname(os.path.abspath(path)))
        if info is None:
            return []
        return [
            e
            for e in cat.entries(cat.closure(info), "genie")
            if not e.shadowed_by and not is_super_genie(e.name)
        ]
    except Exception:  # noqa: BLE001
        from ..infra import logsetup

        logsetup.note_exception("pick_fields.genie_entries")
        return []


class GenieChecklist(QtWidgets.QWidget):
    """A ticked tree of genies grouped by library."""

    changed = QtCore.Signal()
    LINK_ROLE = QtCore.Qt.UserRole

    def __init__(self, path: str, counts: Dict[Tuple[str, str], int], ticked=(), parent=None, entries=None):
        super().__init__(parent)
        self._counts = {(l.lower(), e.lower()): n for (l, e), n in counts.items()}
        self._spelling = {(l.lower(), e.lower()): (l, e) for (l, e) in counts}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QtWidgets.QHBoxLayout()
        from .style.theme import SearchField

        # (the one search box of the UI guide, as every other list's filter)
        self.filter = SearchField("Filter genies", delay_ms=120, enter_filters=True)
        self.here_only = QtWidgets.QCheckBox("Placed here only")
        self.here_only.setToolTip("List only the genies placed here")
        self.here_only.setChecked(bool(counts))
        top.addWidget(self.filter, 1)
        top.addWidget(self.here_only)
        lay.addLayout(top)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Genie", "Here"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(140)
        from .tables import make_resizable

        make_resizable(self.tree, "GenieChecklist/genies", stretch=0, fit_view=True)
        lay.addWidget(self.tree, 1)
        row = QtWidgets.QHBoxLayout()
        self.b_all = QtWidgets.QPushButton("Tick Shown")
        self.b_none = QtWidgets.QPushButton("Untick All")
        self.b_all.setToolTip("Tick every genie shown")
        self.b_none.setToolTip("Untick every genie")
        self.b_all.clicked.connect(lambda: self.tick_all(True))
        self.b_none.clicked.connect(lambda: self.tick_all(False, shown_only=False))
        row.addWidget(self.b_all)
        row.addWidget(self.b_none)
        row.addStretch(1)
        self.count = QtWidgets.QLabel()
        row.addWidget(self.count)
        lay.addLayout(row)
        self._populate(path, entries)
        self.set_ticked(ticked)
        self._apply_filter()
        self.filter.textChanged.connect(lambda _t: self._apply_filter())
        self.here_only.toggled.connect(lambda _on: self._apply_filter())
        self.tree.itemChanged.connect(self._item_changed)
        self._update_count()

    # -- building ----------------------------------------------------------------------
    def _populate(self, path: str, entries) -> None:
        from collections import OrderedDict

        if entries is None:
            entries = genie_entries(path)
        groups: "OrderedDict[str, list]" = OrderedDict()
        listed = set()
        for e in entries:
            key = (e.library.lower(), e.name.lower())
            if key in listed:
                continue
            listed.add(key)
            label = "%s (%s)" % (e.library, e.project) if e.project else e.library
            groups.setdefault(label, []).append(self._spelling.get(key, (e.library, e.name)))
        for key, link in self._spelling.items():
            if key not in listed:  # (placed here, its library not found)
                groups.setdefault("%s (not found)" % link[0], []).append(link)
        self.tree.blockSignals(True)
        try:
            for label, links in groups.items():
                lib = QtWidgets.QTreeWidgetItem([label, ""])
                lib.setFlags(lib.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsAutoTristate)
                lib.setCheckState(0, QtCore.Qt.Unchecked)
                n_here = 0
                for link in sorted(links, key=lambda l: l[1].lower()):
                    n = self._counts.get((link[0].lower(), link[1].lower()), 0)
                    n_here += n
                    it = QtWidgets.QTreeWidgetItem([link[1], str(n) if n else ""])
                    it.setData(0, self.LINK_ROLE, link)
                    it.setToolTip(0, "%s.%s" % link)
                    it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                    it.setCheckState(0, QtCore.Qt.Unchecked)
                    it.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    lib.addChild(it)
                lib.setText(1, str(n_here) if n_here else "")
                lib.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.tree.addTopLevelItem(lib)
        finally:
            self.tree.blockSignals(False)

    def _genie_items(self):
        for k in range(self.tree.topLevelItemCount()):
            lib = self.tree.topLevelItem(k)
            for j in range(lib.childCount()):
                yield lib.child(j)

    # -- the ticks ---------------------------------------------------------------------
    def ticked(self) -> List[Tuple[str, str]]:
        return [
            it.data(0, self.LINK_ROLE) for it in self._genie_items() if it.checkState(0) == QtCore.Qt.Checked
        ]

    def set_ticked(self, links) -> None:
        want = {(l.lower(), e.lower()) for l, e in links}
        self.tree.blockSignals(True)
        try:
            for it in self._genie_items():
                l, e = it.data(0, self.LINK_ROLE)
                it.setCheckState(
                    0, QtCore.Qt.Checked if (l.lower(), e.lower()) in want else QtCore.Qt.Unchecked
                )
                if (l.lower(), e.lower()) in want:
                    it.parent().setExpanded(True)
        finally:
            self.tree.blockSignals(False)
        self._sync_libraries()
        self._update_count()
        self.changed.emit()

    def tick_all(self, on: bool, shown_only: bool = True) -> None:
        self.tree.blockSignals(True)
        try:
            for it in self._genie_items():
                if not shown_only or not it.isHidden():
                    it.setCheckState(0, QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)
        finally:
            self.tree.blockSignals(False)
        self._sync_libraries()
        self._update_count()
        self.changed.emit()

    def _sync_libraries(self) -> None:
        """Each library row's tick from its genies' (signals blocked: the auto tri-state
        follows only clicks)."""
        self.tree.blockSignals(True)
        try:
            for k in range(self.tree.topLevelItemCount()):
                lib = self.tree.topLevelItem(k)
                states = {lib.child(j).checkState(0) for j in range(lib.childCount())}
                lib.setCheckState(0, states.pop() if len(states) == 1 else QtCore.Qt.PartiallyChecked)
        finally:
            self.tree.blockSignals(False)

    def _item_changed(self, _item, column: int) -> None:
        if column == 0:
            self._update_count()
            self.changed.emit()

    def _update_count(self) -> None:
        n = len(self.ticked())
        self.count.setText("%d ticked" % n if n else "")

    def _apply_filter(self) -> None:
        text = self.filter.text().strip().lower()
        here = self.here_only.isChecked()
        for k in range(self.tree.topLevelItemCount()):
            lib = self.tree.topLevelItem(k)
            shown = 0
            for j in range(lib.childCount()):
                it = lib.child(j)
                l, e = it.data(0, self.LINK_ROLE)
                ok = (not here or self._counts.get((l.lower(), e.lower()), 0) > 0) and (
                    not text or text in ("%s.%s" % (l, e)).lower()
                )
                it.setHidden(not ok)
                shown += ok
            lib.setHidden(shown == 0)
            if text and shown:
                lib.setExpanded(True)


class WideCombo(QtWidgets.QComboBox):
    """A read-only drop-down of long names (a project filter, 'Name (count)'): the box is
    as wide as its longest item and grows with its row (horizontally Expanding), and the list opens as wide as the names (widen_popup)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, self.sizePolicy().verticalPolicy())

    def showPopup(self) -> None:  # noqa: N802 -- Qt naming
        widen_popup(self)
        super().showPopup()


class PageField(QtWidgets.QComboBox):
    """A page name picker."""

    def __init__(
        self,
        project_dir: Optional[str] = None,
        parent=None,
        *,
        value: str = "",
        exclude: str = "",
        system_pages: Optional[bool] = None,
        limit: int = 0,
        pages=None,
    ):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        # a fixed width (sized to its longest page name it would widen the Properties
        # panel); the list opens as wide as the names instead (showPopup)
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(16)
        # a page name field grows with its dialog
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, self.sizePolicy().verticalPolicy())
        if system_pages is None:
            from .prefs.settings import option

            system_pages = option("list_system_pages")
        self.addItem(NONE_TEXT, "")
        # (what fill() lists)
        self._source = (
            project_dir,
            None if pages is None else list(pages),
            exclude or "",
            bool(system_pages),
        )
        self._filled = False
        if limit:
            self.lineEdit().setMaxLength(max(int(limit), len(value or "")))
        comp = self.completer()
        if comp is not None:
            comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            comp.setFilterMode(QtCore.Qt.MatchContains)
            comp.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self.set_value(value)

    def fill(self) -> None:
        """List the page names now (once; the text typed or set stays)."""
        if self._filled:
            return
        self._filled = True
        project_dir, pages, exclude, system_pages = self._source
        rows = [(p, "") for p in pages] if pages is not None else _page_names(project_dir)
        low_ex = exclude.lower()
        text = self.lineEdit().text()
        blocked = self.blockSignals(True)
        try:
            for name, proj in rows:
                if name.lower() == low_ex:
                    continue
                if name.startswith("!") and not system_pages:
                    continue
                self.addItem(name, name)
                if proj:
                    self.setItemData(self.count() - 1, "Project %s" % proj, QtCore.Qt.ToolTipRole)
            if self.lineEdit().text() != text:
                self.setEditText(text)
        finally:
            self.blockSignals(blocked)

    def is_filled(self) -> bool:
        """The page names are listed (tests)."""
        return self._filled

    def showPopup(self) -> None:
        self.fill()
        widen_popup(self)
        super().showPopup()

    def focusInEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        self.fill()  # (the completer completes from the listed names)
        super().focusInEvent(ev)

    def wheelEvent(self, ev) -> None:  # noqa: N802
        self.fill()
        super().wheelEvent(ev)

    def pages(self) -> List[str]:
        """The listed page names."""
        self.fill()
        return [self.itemText(i) for i in range(1, self.count())]

    def set_value(self, value: str) -> None:
        self.setEditText(value or NONE_TEXT)

    def value(self) -> str:
        """The page name ('' for '<None>')."""
        t = self.currentText().strip()
        return "" if t in ("", NONE_TEXT) else t


# ------------------------------------------------------------------ LibraryElementField
class LibraryElementField(QtWidgets.QWidget):
    """A library element picker with thumbnail and Browse."""

    changed = QtCore.Signal()
    editRequested = QtCore.Signal()

    THUMB = QtCore.QSize(64, 64)

    def __init__(
        self,
        project_dir: Optional[str] = None,
        kind: str = "symbol",
        parent=None,
        *,
        title: str = "Select Symbol",
        clear_text: str = "",
        edit_text: str = "",
        empty_text: str = "",
        resolve: Optional[Callable] = None,
        picker: Optional[Callable] = None,
        validate: Optional[Callable] = None,
        thumb: Optional[QtCore.QSize] = None,
    ):
        super().__init__(parent)
        self.project_dir = project_dir
        self.kind = kind
        self.title = title
        self._resolve = resolve
        self._picker = picker
        self._validate = validate
        self._empty_text = empty_text
        self._default_pm: Optional[QtGui.QPixmap] = None
        self._value: Optional[Tuple[str, str]] = None
        self._path: Optional[str] = None
        self._ro = False
        size = QtCore.QSize(thumb or self.THUMB)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.picture = QtWidgets.QLabel()
        self.picture.setFixedSize(size + QtCore.QSize(8, 8))
        self.picture.setAlignment(QtCore.Qt.AlignCenter)
        self.picture.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._thumb = size
        lay.addWidget(self.picture, 0, QtCore.Qt.AlignTop)
        right = QtWidgets.QVBoxLayout()
        self.name = QtWidgets.QLabel()
        self.name.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        f = self.name.font()
        f.setBold(True)
        self.name.setFont(f)
        right.addWidget(self.name)
        from .style import theme

        self.detail = theme.note_label("")
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.detail.hide()
        right.addWidget(self.detail)
        row = QtWidgets.QHBoxLayout()
        self.btn_browse = QtWidgets.QPushButton("Browse…")
        self.btn_browse.setToolTip("Choose a library element")
        self.btn_browse.clicked.connect(self.browse)
        row.addWidget(self.btn_browse)
        self.btn_clear = None
        if clear_text:
            self.btn_clear = QtWidgets.QPushButton(clear_text)
            self.btn_clear.clicked.connect(self.clear)
            row.addWidget(self.btn_clear)
        self.btn_edit = None
        if edit_text:
            self.btn_edit = QtWidgets.QPushButton(edit_text)
            self.btn_edit.setToolTip("Open the library element for editing")
            self.btn_edit.clicked.connect(self.editRequested.emit)
            row.addWidget(self.btn_edit)
        row.addStretch(1)
        right.addLayout(row)
        right.addStretch(1)
        lay.addLayout(right, 1)
        self._show()

    # -- values ---------------------------------------------------------------------
    def value(self) -> Optional[Tuple[str, str]]:
        """(library, element), or None."""
        return self._value

    def path(self) -> Optional[str]:
        """The library file of the element, when known."""
        return self._path

    def text(self) -> str:
        """'lib.element' ('' when empty)."""
        return "%s.%s" % self._value if self._value else ""

    def setText(self, text: str) -> None:  # noqa: N802 -- line-edit style API
        """Set 'lib.element' (as typed; '' clears), with a ``changed``."""
        text = (text or "").strip()
        lib, dot, elem = text.rpartition(".")  # (the last dot, as CtDraw splits a link)
        if not text:
            self.set_value(None, emit=True)
        elif dot and lib and elem:
            self.set_value((lib, elem), emit=True)
        else:
            self._value, self._path = (text, ""), None
            self._show()
            self.changed.emit()

    def set_value(self, value, path: Optional[str] = None, *, emit: bool = False) -> None:
        """Show ``value`` ((library, element) or None) without asking."""
        self._value = (str(value[0]), str(value[1])) if value else None
        self._path = path
        if self._value and not path and self._resolve is not None:
            try:
                self._path = self._resolve(*self._value) or None
            except Exception:  # noqa: BLE001 -- no thumbnail then
                _note_exc("pick_fields.ElementPreview/set_value")
                self._path = None
        self._show()
        if emit:
            self.changed.emit()

    def set_default_picture(self, pixmap: Optional[QtGui.QPixmap]) -> None:
        """What the thumbnail shows while the field is empty."""
        self._default_pm = pixmap
        self._show()

    def set_detail(self, text: str, role: str = "text_dim") -> None:
        """A line under the name (the library file, 'not found' ...)."""
        from .style import theme

        theme.set_note_role(self.detail, role)
        self.detail.setText(text or "")
        self.detail.setVisible(bool(text))

    def _show(self) -> None:
        if self._value:
            self.name.setText(self.text())
        else:
            self.name.setText(self._empty_text)
        pm = (
            element_thumbnail(self._path, self._value[1], self._thumb) if self._value and self._path else None
        )
        if pm is None and not self._value:
            pm = self._default_pm
        if pm is not None and not pm.isNull():
            if pm.width() > self._thumb.width() or pm.height() > self._thumb.height():
                pm = pm.scaled(self._thumb, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            self.picture.setText("")
            self.picture.setPixmap(pm)
        else:
            self.picture.setPixmap(QtGui.QPixmap())
            self.picture.setText("")
        if self.btn_clear is not None:
            self.btn_clear.setEnabled(not self._ro and self._value is not None)
        if self.btn_edit is not None:
            self.btn_edit.setEnabled(bool(self._path))

    # -- actions --------------------------------------------------------------------
    def browse(self) -> bool:
        """Browse...: pick an element; True when one was set."""
        if self._ro:
            return False
        if self._picker is not None:
            pick = self._picker(self)
        else:
            from .browser import PageBrowserDialog

            pick = PageBrowserDialog.get_library_element(self, self.project_dir, self.kind, title=self.title)
        if not pick:
            return False
        path, element = pick[0], pick[1]
        lib = library_stem(path) if path and os.path.splitext(path)[1] else str(path)
        if self._validate is not None:
            why = self._validate(lib, element, path)
            if why:
                warn(self, self.title, why)
                return False
        full = path if path and os.path.splitext(path)[1] else None
        self.set_value((lib, element), full, emit=True)
        return True

    def clear(self) -> None:
        if self._ro:
            return
        self.set_value(None, emit=True)

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        self.btn_browse.setEnabled(not ro)
        self._show()


from ..infra import caches as _caches  # noqa: E402

_caches.register("pick_fields.template_catalogs", _TEMPLATE_CATALOGS, on=("refresh", "libraries", "tests"))
