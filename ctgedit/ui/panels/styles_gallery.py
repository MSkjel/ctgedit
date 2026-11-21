"""Named styles: store, gallery and commands."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import json
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..editor import draw_styles as DS
from . import panel_options as _panels

STYLES_KEY = "styles/list"
SHARED_KEY = "styles/shared_file"
FILE_FORMAT = 1
FILE_SUFFIX = ".ctgstyles"
FILE_FILTER = "Styles (*.ctgstyles);;JSON files (*.json)"
#: a tile's size at 100 % (px)
TILE_W, TILE_H = 48, 32
#: the kinds of a style's family: what 'Set as Default for New Objects' offers it for
SHAPE_KINDS = ("line", "pipe", "rectangle", "ellipse", "polygon")
TEXT_KINDS = ("text", "number", "button")


def _note_exc(where: str) -> None:
    from ...infra.logsetup import note_exception

    note_exception("styles_gallery." + where)


# --------------------------------------------------------------------------- styles
@dataclass
class Style:
    """A named style: its appearance (DocumentProperties.appearance_of), the kind it was
    taken from ('' = not known), whether it comes from the shared file (read-only)."""

    name: str
    appearance: dict = field(default_factory=dict)
    kind: str = ""
    shared: bool = False

    def family(self) -> str:
        """'text' (a font) or 'shape'."""
        if self.kind in TEXT_KINDS:
            return "text"
        if self.kind in SHAPE_KINDS:
            return "shape"
        return "text" if (self.appearance or {}).get("font") else "shape"

    def kinds(self) -> tuple:
        return TEXT_KINDS if self.family() == "text" else SHAPE_KINDS

    def as_json(self) -> dict:
        out = {"name": self.name, "appearance": dict(self.appearance)}
        if self.kind:
            out["kind"] = self.kind
        return out


def _styles_from(items, shared: bool = False) -> List[Style]:
    out, seen = [], set()
    for s in items or ():
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        app = s.get("appearance")
        if not name or not isinstance(app, dict) or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(Style(name, dict(app), str(s.get("kind") or ""), shared))
    return out


def read_style_file(path: str) -> List[Style]:
    """The styles of a .ctgstyles file (raises OSError / ValueError when it cannot be
    read)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("styles") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("The file holds no styles.")
    return _styles_from(items)


def write_style_file(path: str, styles: List[Style]) -> None:
    data = {"format": FILE_FORMAT, "styles": [s.as_json() for s in styles]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


class StyleStore(QtCore.QObject):
    """The user's styles and the shared file's (module doc)."""

    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watcher = QtCore.QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._file_changed)
        self._watcher.directoryChanged.connect(self._file_changed)
        self._path = None
        self._shared: List[Style] = []
        #: why the shared file could not be read ('' = it was read or none is set)
        self.shared_error = ""
        self._loaded = False

    # -- the user's styles ----------------------------------------------------------------
    @staticmethod
    def user_styles() -> List[Style]:
        from ..prefs.settings import app_settings

        try:
            raw = app_settings().value(STYLES_KEY, "[]")
            items = json.loads(raw) if isinstance(raw, str) else list(raw or ())
        except Exception:  # noqa: BLE001  # swallow-ok: a damaged list is an empty one
            items = []
        return _styles_from(items)

    def put_user_styles(self, styles: List[Style]) -> None:
        from ..prefs.settings import app_settings

        app_settings().setValue(STYLES_KEY, json.dumps([s.as_json() for s in styles if not s.shared]))
        self.changed.emit()

    # -- the shared file --------------------------------------------------------------------
    @staticmethod
    def shared_path() -> str:
        from ..prefs.settings import app_settings

        return str(app_settings().value(SHARED_KEY, "") or "").strip()

    def shared_styles(self) -> List[Style]:
        if not self._loaded or self._path != self.shared_path():
            self._load_shared()
        return list(self._shared)

    def _load_shared(self) -> None:
        path = self.shared_path()
        self._loaded = True
        old = self._watcher.files() + self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        self._path = path
        self._shared = []
        self.shared_error = ""
        if not path:
            return
        folder = os.path.dirname(os.path.abspath(path))
        if os.path.isdir(folder):
            self._watcher.addPath(folder)
        if not os.path.isfile(path):
            self.shared_error = "The shared styles file %s is not there." % path
            return
        self._watcher.addPath(path)
        try:
            styles = read_style_file(path)
        except (OSError, ValueError) as exc:
            self.shared_error = "The shared styles file could not be read: %s" % exc
            return
        for s in styles:
            s.shared = True
        self._shared = styles

    def _file_changed(self, _path: str = "") -> None:
        # (an editor replaces the file: the watch is set again by the reload)
        QtCore.QTimer.singleShot(200, self, self.reload)

    def reload(self) -> None:
        """Read the shared file again (the setting or the file changed)."""
        before = [(s.name, s.appearance) for s in self._shared]
        self._load_shared()
        if [(s.name, s.appearance) for s in self._shared] != before:
            self.changed.emit()

    # -- all --------------------------------------------------------------------------------
    def all(self) -> List[Style]:
        """The user's styles, then the shared ones."""
        return self.user_styles() + self.shared_styles()

    def find(self, name: str, shared: Optional[bool] = None) -> Optional[Style]:
        for s in self.all():
            if s.name == name and (shared is None or s.shared == shared):
                return s
        return None


_store: Optional[StyleStore] = None


def store() -> StyleStore:
    """The process's style store (made on first use)."""
    global _store
    if _store is None:
        _store = StyleStore()
    return _store


# --------------------------------------------------------------------------- matching
def matches(style: Style, appearance: dict, kind: Optional[str]) -> bool:
    """An object of ``kind`` with ``appearance`` has the look of ``style``: its family's
    fields the style sets are all the same (Select Objects with This Style)."""
    if kind is None or kind not in style.kinds():
        return False
    fields = DS.KIND_FIELDS.get(kind, ())
    compared = 0
    for k in fields:
        v = style.appearance.get(k)
        if v is None or (k == "font" and not v) or (k == "font_size" and not v):
            continue
        if k == "fill_colour" and style.appearance.get("filled") is False:
            continue
        if k == "gradient_colour" and not style.appearance.get("gradient_mode"):
            continue
        compared += 1
        got = appearance.get(k)
        if k in DS.COLOUR_FIELDS:
            got = None if got is None else int(got) & 0xFFFFFF
            v = int(v) & 0xFFFFFF
        if got != v:
            return False
    return compared > 0


# --------------------------------------------------------------------------- tiles
def _qc(c, alpha: int = 255) -> QtGui.QColor:
    c = int(c) & 0xFFFFFF
    return QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF, alpha)


_PEN_STYLES = (
    QtCore.Qt.SolidLine,
    QtCore.Qt.DashLine,
    QtCore.Qt.DotLine,
    QtCore.Qt.DashDotLine,
    QtCore.Qt.DashDotDotLine,
)


def paint_tile(
    p: QtGui.QPainter,
    rect: QtCore.QRectF,
    appearance: dict,
    page_colour: int = DS.DEFAULT_PAGE,
    family: str = "",
) -> None:
    """A style's tile in ``rect``: the page colour, a shape in the fill / line colour and
    width (a text style: a large 'Ag'), and an 'Ag' in the text font and colour."""
    a = appearance or {}
    family = family or ("text" if a.get("font") else "shape")
    p.save()
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.fillRect(rect, _qc(page_colour))
    light = DS.lightness(page_colour) > DS.LIGHT_ABOVE
    ink = 0x202020 if light else 0xE0E0E0
    if family == "shape":
        box = QtCore.QRectF(rect.left() + 4, rect.top() + 4, rect.width() * 0.42, rect.height() - 8)
        lw = max(1, min(4, int(a.get("line_width") or 1)))
        pen = QtGui.QPen(_qc(a["line_colour"]) if a.get("line_colour") is not None else _qc(ink), lw)
        pen.setStyle(
            _PEN_STYLES[int(a.get("line_style") or 0) % len(_PEN_STYLES)] if lw == 1 else QtCore.Qt.SolidLine
        )
        p.setPen(pen)
        filled = a.get("filled")
        if filled is not False and a.get("fill_colour") is not None:
            if a.get("gradient_mode") and a.get("gradient_colour") is not None:
                g = QtGui.QLinearGradient(box.topLeft(), box.topRight())
                g.setColorAt(0.0, _qc(a["fill_colour"]))
                g.setColorAt(1.0, _qc(a["gradient_colour"]))
                p.setBrush(QtGui.QBrush(g))
            else:
                p.setBrush(_qc(a["fill_colour"]))
        else:
            p.setBrush(QtCore.Qt.NoBrush)
        r = min(8.0, float(a.get("corner_radius") or 0) / 2.0)
        inner = box.adjusted(lw / 2.0, lw / 2.0, -lw / 2.0, -lw / 2.0)
        p.drawRoundedRect(inner, r, r)
        text_rect = QtCore.QRectF(box.right() + 2, rect.top(), rect.right() - box.right() - 3, rect.height())
        text_colour = a.get("text_colour")
        size = rect.height() * 0.42
    else:
        text_rect = rect.adjusted(2, 0, -2, 0)
        text_colour = a.get("text_colour")
        size = rect.height() * 0.62
    f = QtGui.QFont(str(a.get("font") or "Arial"))
    f.setPixelSize(max(6, int(size)))
    f.setBold(bool(a.get("bold")))
    f.setItalic(bool(a.get("italic")))
    f.setUnderline(bool(a.get("underline")))
    f.setStrikeOut(bool(a.get("strikeout")))
    p.setFont(f)
    p.setPen(_qc(text_colour if text_colour is not None else ink))
    align = (
        {"left": QtCore.Qt.AlignLeft, "right": QtCore.Qt.AlignRight}.get(
            a.get("justify") or "", QtCore.Qt.AlignHCenter
        )
        if family == "text"
        else QtCore.Qt.AlignHCenter
    )
    p.drawText(text_rect.adjusted(2, 0, -2, 0), align | QtCore.Qt.AlignVCenter, "Ag")
    p.restore()


def tile_pixmap(
    style: Style, w: int = TILE_W, h: int = TILE_H, page_colour: int = DS.DEFAULT_PAGE, dpr: float = 1.0
) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    paint_tile(p, QtCore.QRectF(0, 0, w, h), style.appearance, page_colour, style.family())
    from ..style.theme import color

    p.setPen(QtGui.QPen(color("control_edge"), 1))
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRect(QtCore.QRectF(0.5, 0.5, w - 1, h - 1))
    if style.shared:
        # a small corner mark: from the shared file
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(color("accent"))
        p.drawPolygon(
            QtGui.QPolygonF([QtCore.QPointF(w - 9, 1), QtCore.QPointF(w - 1, 1), QtCore.QPointF(w - 1, 9)])
        )
    p.end()
    return pm


def tile_icon(style: Style, page_colour: int = DS.DEFAULT_PAGE) -> QtGui.QIcon:
    """The tile as an icon, at the UI's text size (a larger text size: larger tiles)."""
    from ..style.theme import px

    w, h = px(TILE_W), px(TILE_H)
    ic = QtGui.QIcon()
    for dpr in (1.0, 2.0):
        ic.addPixmap(tile_pixmap(style, w, h, page_colour, dpr))
    return ic


def describe(style: Style) -> str:
    """The tile's tool tip: the name and what it sets."""
    a = style.appearance or {}
    parts = []
    if style.family() == "text":
        if a.get("font"):
            parts.append("%s %s pt" % (a["font"], a.get("font_size") or ""))
        flags = [
            n for k, n in (("bold", "bold"), ("italic", "italic"), ("underline", "underlined")) if a.get(k)
        ]
        parts.extend(flags)
        if a.get("text_colour") is not None:
            parts.append("text #%06X" % (int(a["text_colour"]) & 0xFFFFFF))
    else:
        if a.get("line_width"):
            parts.append("line %s px" % a["line_width"])
        if a.get("line_colour") is not None:
            parts.append("#%06X" % (int(a["line_colour"]) & 0xFFFFFF))
        if a.get("filled") is False:
            parts.append("no fill")
        elif a.get("fill_colour") is not None:
            parts.append("fill #%06X" % (int(a["fill_colour"]) & 0xFFFFFF))
        if a.get("corner_radius"):
            parts.append("corners %s px" % a["corner_radius"])
    text = style.name + ("\n" + ", ".join(str(x) for x in parts) if parts else "")
    if style.shared:
        text += "\nFrom the shared styles file (read-only)"
    return text


class StyleTile(QtWidgets.QToolButton):
    """One style's tile. ``hoverChanged(name)`` as the pointer comes ('' as it goes)."""

    hoverChanged = QtCore.Signal(str)

    def __init__(
        self,
        style: Style,
        page_colour: int = DS.DEFAULT_PAGE,
        parent=None,
        action: Optional[QtGui.QAction] = None,
    ):
        super().__init__(parent)
        from ..style.theme import px

        self.style_ = style
        self.setObjectName("styleTile")
        if action is not None:
            self.setDefaultAction(action)  # (its icon is the style's tile)
        else:
            self.setIcon(tile_icon(style, page_colour))
        self.setIconSize(QtCore.QSize(px(TILE_W), px(TILE_H)))
        self.setFixedSize(px(TILE_W) + 4, px(TILE_H) + 4)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.setAutoRaise(True)
        self.setToolTip(describe(style))
        self.setAccessibleName("Style %s" % style.name)
        self.setFocusPolicy(QtCore.Qt.TabFocus)

    def enterEvent(self, e) -> None:  # noqa: N802 -- Qt API
        self.hoverChanged.emit(self.style_.name)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 -- Qt API
        self.hoverChanged.emit("")
        super().leaveEvent(e)


_TILE_QSS = """
QToolButton#styleTile { border: 1px solid transparent; border-radius: 3px; padding: 1px; }
QToolButton#styleTile:hover { border-color: #4da3ff; background: #2c3036; }
QToolButton#styleTile[kbdFocus="true"] { border: 2px solid #4da3ff; padding: 0px; }
QToolButton#styleMore { border: 1px solid #3a3f46; border-radius: 3px; padding: 0px; }
QToolButton#styleMore:hover { border-color: #4da3ff; }
QToolButton#styleMore[kbdFocus="true"] { border: 2px solid #4da3ff; }
"""


class StylesGallery(QtWidgets.QWidget):
    """The style tiles."""

    def __init__(
        self,
        mode: str = "ribbon",
        parent=None,
        *,
        columns: int = 4,
        actions_fn: Optional[Callable] = None,
        preview_fn: Optional[Callable] = None,
        menu_fn: Optional[Callable] = None,
        empty_action: Optional[QtGui.QAction] = None,
        page_colour_fn: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.setObjectName("stylesGallery")
        self.mode = mode
        self.columns = max(1, int(columns))
        self._compact = False
        self._actions_fn = actions_fn or (lambda: {})
        self._preview_fn = preview_fn
        self._menu_fn = menu_fn
        self._empty_action = empty_action
        self._page_colour_fn = page_colour_fn or (lambda: DS.DEFAULT_PAGE)
        self.setStyleSheet(_TILE_QSS)
        self.tiles: List[StyleTile] = []
        self._more = None
        if mode == "strip":
            from ..flow_layout import FlowLayout

            self._lay = FlowLayout(self, spacing=2)
        else:
            self._lay = QtWidgets.QGridLayout(self)
            self._lay.setContentsMargins(0, 0, 0, 0)
            self._lay.setSpacing(_sp(0))
        self.rebuild()
        store().changed.connect(self.rebuild)

    def styles(self) -> List[Style]:
        return store().all()

    def set_compact(self, on: bool) -> None:
        """The ribbon group is compact: two tiles and the button with every style."""
        on = bool(on)
        if on != self._compact:
            self._compact = on
            self.rebuild()

    def _clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.tiles = []
        self._more = None

    def rebuild(self) -> None:
        """The tiles again (the styles, the page colour or the actions changed)."""
        try:
            self._clear()
            styles = self.styles()
            try:
                page = int(self._page_colour_fn())
            except Exception:  # noqa: BLE001  # swallow-ok: no page: the default page colour
                page = DS.DEFAULT_PAGE
            acts = {}
            try:
                acts = dict(self._actions_fn() or {})
            except Exception:  # noqa: BLE001
                _note_exc("rebuild.actions")
            if not styles:
                if self._empty_action is not None and not self._compact:
                    # (no style yet: a dashed tile says how to make one)
                    b = QtWidgets.QLabel("No styles yet", self)
                    b.setObjectName("styleEmpty")
                    b.setAlignment(QtCore.Qt.AlignCenter)
                    b.setWordWrap(True)
                    from ..style.theme import px, set_note_role

                    set_note_role(
                        b, "text_dim", "border: 1px dashed #6e7681; border-radius: 3px; padding: 2px 4px;"
                    )
                    b.setFixedSize(px(TILE_W) + 8, px(TILE_H) + 4)
                    b.setToolTip("Select an object and choose Save Appearance as Style to keep its look here")
                    self._add(b, 0)
                return
            limit = (
                (min(2, self.columns) if self._compact else self.columns)
                if self.mode == "ribbon"
                else len(styles)
            )
            shown = styles[:limit] if len(styles) <= limit else styles[: limit - 1]
            for k, s in enumerate(shown):
                key = ("shared:" if s.shared else "") + s.name
                t = StyleTile(s, page, self, acts.get(key))
                t.hoverChanged.connect(self._hover)
                t.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                t.customContextMenuRequested.connect(lambda pos, tile=t: self._menu(tile, pos))
                self.tiles.append(t)
                self._add(t, k)
            if len(shown) < len(styles):
                self._more = QtWidgets.QToolButton(self)
                self._more.setObjectName("styleMore")
                self._more.setText("+%d" % (len(styles) - len(shown)))
                self._more.setToolTip("Every style")
                self._more.setAccessibleName("Every style")
                from ..style.theme import px

                self._more.setFixedSize(px(TILE_W) + 4, px(TILE_H) + 4)
                self._more.clicked.connect(self._show_all)
                self._add(self._more, len(shown))
        except RuntimeError:  # swallow-ok: the gallery is being destroyed
            return
        self.updateGeometry()

    def _add(self, w, k: int) -> None:
        if self.mode == "ribbon":
            self._lay.addWidget(w, 0, k)
        else:
            self._lay.addWidget(w)

    def _hover(self, name: str) -> None:
        if self._preview_fn is not None:
            try:
                self._preview_fn(name)
            except Exception:  # noqa: BLE001  # one preview failing must not stop the pointer
                _note_exc("hover")

    def _menu(self, tile: StyleTile, pos) -> None:
        if self._menu_fn is None:
            return
        m = QtWidgets.QMenu(self)
        try:
            self._menu_fn(tile.style_, m)
        except Exception:  # noqa: BLE001
            _note_exc("menu")
            return
        if not m.isEmpty():
            m.exec(tile.mapToGlobal(pos))

    def _show_all(self) -> None:
        """Every style in a pop-up (the ribbon shows the first ones)."""
        pop = QtWidgets.QFrame(self, QtCore.Qt.Popup)
        pop.setObjectName("stylesPopup")
        pop.setStyleSheet(
            "QFrame#stylesPopup { background: #23262b; border: 1px solid #3a3f46; border-radius: 6px; }"
        )
        lay = QtWidgets.QVBoxLayout(pop)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        g = StylesGallery(
            "strip",
            pop,
            actions_fn=self._actions_fn,
            preview_fn=self._preview_fn,
            menu_fn=self._menu_fn,
            page_colour_fn=self._page_colour_fn,
        )
        from ..style.theme import px

        g.setFixedWidth(6 * (px(TILE_W) + 6))
        for t in g.tiles:
            if t.defaultAction() is not None:
                t.defaultAction().triggered.connect(pop.close)
        lay.addWidget(g)
        pop.adjustSize()
        pop.move(self._more.mapToGlobal(QtCore.QPoint(0, self._more.height() + 2)))
        pop.show()

    def sizeHint(self) -> QtCore.QSize:  # noqa: D102 -- Qt API
        if self.mode != "ribbon":
            return super().sizeHint()
        from ..style.theme import px

        n = len(self.tiles) + (1 if self._more is not None else 0)
        if not n:
            return super().sizeHint()
        return QtCore.QSize(n * (px(TILE_W) + 6), px(TILE_H) + 6)


# --------------------------------------------------------------------------- Manage Styles
class ManageStylesDialog(QtWidgets.QDialog):
    """Edit > Styles > Manage Styles…: the styles with their tiles; Rename…, Remove, Move
    Up / Down, Import… / Export…. The shared file's styles are listed after a separator,
    read-only. OK keeps the changes (:meth:`styles`)."""

    def __init__(self, parent=None, page_colour: int = DS.DEFAULT_PAGE):
        super().__init__(parent)
        self.setWindowTitle("Manage Styles")
        self._page = page_colour
        self._styles = store().user_styles()
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Click a style on the ribbon or in Edit > Styles to apply it to the selection."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.list = QtWidgets.QListWidget()
        self.list.setAccessibleName("Styles")
        from ..style.theme import px

        self.list.setIconSize(QtCore.QSize(px(TILE_W), px(TILE_H)))
        self.list.setSpacing(1)
        lay.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        self.btn_rename = QtWidgets.QPushButton("Rename…")
        self.btn_remove = QtWidgets.QPushButton("Remove")
        self.btn_up = QtWidgets.QPushButton("Move Up")
        self.btn_down = QtWidgets.QPushButton("Move Down")
        for b in (self.btn_rename, self.btn_remove, self.btn_up, self.btn_down):
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        self.btn_import = QtWidgets.QPushButton("Import…")
        self.btn_import.setToolTip("Add the styles of a styles file")
        self.btn_export = QtWidgets.QPushButton("Export…")
        self.btn_export.setToolTip("Save your styles in a file, for example for your team")
        for b in (self.btn_import, self.btn_export):
            b.setAutoDefault(False)
            row2.addWidget(b)
        row2.addStretch(1)
        lay.addLayout(row2)
        from ..style import theme

        self.note = theme.note_label("", parent=self)
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.btn_rename.clicked.connect(self.rename_selected)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_import.clicked.connect(self._import)
        self.btn_export.clicked.connect(self._export)
        self.list.itemSelectionChanged.connect(self._sync)
        self.list.itemDoubleClicked.connect(lambda _it: self.rename_selected())
        self._fill()

    def styles(self) -> List[Style]:
        """The user's styles as the dialog shows them."""
        return list(self._styles)

    def _fill(self, select: int = -1) -> None:
        self.list.clear()
        for s in self._styles:
            it = QtWidgets.QListWidgetItem(tile_icon(s, self._page), s.name)
            it.setToolTip(describe(s))
            self.list.addItem(it)
        shared = store().shared_styles()
        if shared or store().shared_error:
            head = QtWidgets.QListWidgetItem("Shared: %s" % os.path.basename(store().shared_path()))
            head.setFlags(QtCore.Qt.NoItemFlags)
            head.setToolTip(store().shared_path())
            self.list.addItem(head)
            for s in shared:
                it = QtWidgets.QListWidgetItem(tile_icon(s, self._page), s.name)
                it.setFlags(QtCore.Qt.ItemIsEnabled)
                it.setToolTip(describe(s))
                self.list.addItem(it)
        self.note.setText(store().shared_error)
        self.note.setVisible(bool(store().shared_error))
        if 0 <= select < len(self._styles):
            self.list.setCurrentRow(select)
        self._sync()

    def _row(self) -> int:
        r = self.list.currentRow()
        return r if 0 <= r < len(self._styles) and self.list.item(r).isSelected() else -1

    def _sync(self) -> None:
        r = self._row()
        self.btn_rename.setEnabled(r >= 0)
        self.btn_remove.setEnabled(r >= 0)
        self.btn_up.setEnabled(r > 0)
        self.btn_down.setEnabled(0 <= r < len(self._styles) - 1)
        self.btn_export.setEnabled(bool(self._styles))

    def rename_selected(self, new_name: Optional[str] = None) -> bool:
        r = self._row()
        if r < 0:
            return False
        s = self._styles[r]
        if new_name is None:
            new_name, ok = QtWidgets.QInputDialog.getText(self, "Rename Style", "New name:", text=s.name)
            if not ok:
                return False
        new_name = (new_name or "").strip()
        if not new_name or new_name == s.name:
            return False
        if any(o.name.lower() == new_name.lower() for k, o in enumerate(self._styles) if k != r):
            self.note.setText("There is a style called '%s' already." % new_name)
            self.note.setVisible(True)
            return False
        s.name = new_name
        self._fill(r)
        return True

    def remove_selected(self) -> bool:
        r = self._row()
        if r < 0:
            return False
        del self._styles[r]
        self._fill(min(r, len(self._styles) - 1))
        return True

    def move_selected(self, step: int) -> bool:
        r = self._row()
        k = r + step
        if r < 0 or not 0 <= k < len(self._styles):
            return False
        self._styles[r], self._styles[k] = self._styles[k], self._styles[r]
        self._fill(k)
        return True

    def import_file(self, path: str) -> int:
        """Add the styles of ``path`` (a style of the same name is replaced); how many."""
        try:
            got = read_style_file(path)
        except (OSError, ValueError) as exc:
            self.note.setText("Could not read %s: %s" % (os.path.basename(path), exc))
            self.note.setVisible(True)
            return 0
        names = {s.name.lower(): k for k, s in enumerate(self._styles)}
        for s in got:
            s.shared = False
            k = names.get(s.name.lower())
            if k is None:
                names[s.name.lower()] = len(self._styles)
                self._styles.append(s)
            else:
                self._styles[k] = s
        self._fill()
        self.note.setText("Imported %d style%s." % (len(got), "" if len(got) == 1 else "s"))
        self.note.setVisible(True)
        return len(got)

    def export_file(self, path: str) -> bool:
        try:
            write_style_file(path, self._styles)
        except OSError as exc:
            self.note.setText("Could not save %s: %s" % (os.path.basename(path), exc))
            self.note.setVisible(True)
            return False
        self.note.setText(
            "Saved %d style%s in %s."
            % (len(self._styles), "" if len(self._styles) == 1 else "s", os.path.basename(path))
        )
        self.note.setVisible(True)
        return True

    def _import(self) -> None:
        path, _f = QtWidgets.QFileDialog.getOpenFileName(self, "Import Styles", "", FILE_FILTER)
        if path:
            self.import_file(path)

    def _export(self) -> None:
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Styles", "styles" + FILE_SUFFIX, FILE_FILTER
        )
        if path:
            if not os.path.splitext(path)[1]:
                from ..messages import with_extension

                path = with_extension(self, "Export Styles", path, FILE_SUFFIX)
                if not path:
                    return
            self.export_file(path)


class SaveStyleDialog(QtWidgets.QDialog):
    """Save Appearance as Style…: the tile of the look and its name (a name in use is
    replaced, the dialog says so)."""

    def __init__(self, appearance: dict, kind: str = "", parent=None, page_colour: int = DS.DEFAULT_PAGE):
        super().__init__(parent)
        self.setWindowTitle("Save Style")
        self._style = Style("", dict(appearance or {}), kind)
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        tile = QtWidgets.QLabel()
        from ..style.theme import px

        tile.setPixmap(tile_pixmap(self._style, px(TILE_W) * 2, px(TILE_H) * 2, page_colour))
        row.addWidget(tile)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.name.setMaxLength(64)
        self.name.setPlaceholderText("For example Pipe 3 px or Tank label")
        form.addRow("Name:", self.name)
        row.addLayout(form, 1)
        lay.addLayout(row)
        from ..style import theme

        self.note = theme.note_label("", parent=self)
        lay.addWidget(self.note)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.name.textChanged.connect(self._sync)
        self._sync()

    def _sync(self) -> None:
        name = self.name.text().strip()
        taken = any(s.name.lower() == name.lower() for s in store().user_styles())
        self.note.setText("This replaces the style '%s'." % name if name and taken else "")
        self.buttons.button(QtWidgets.QDialogButtonBox.Save).setEnabled(bool(name))


class SharedStylesRow(QtWidgets.QWidget):
    """Settings > Pages and libraries 'Shared styles file:': the path and Browse…."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit(store().shared_path())
        self.edit.setPlaceholderText("None")
        self.edit.setMinimumWidth(140)
        self.edit.setToolTip(
            "A styles file your team shares, for example on a network "
            "folder. Its styles are listed in Edit > Styles, read-only"
        )
        browse = QtWidgets.QPushButton("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(browse)

    def value(self) -> str:
        return self.edit.text().strip()

    def set_value(self, path: str) -> None:
        if self.edit.text() != path:
            self.edit.setText(path)

    def _browse(self) -> None:
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Shared Styles File", self.value(), FILE_FILTER
        )
        if path:
            self.edit.setText(path)


# --------------------------------------------------------------------------- the preview
def paint_preview(view, painter: QtGui.QPainter, _exposed) -> None:
    """The canvas overlay of a style being pointed at: each selected object as the style
    would make it (its fill, line colour and width; a text tinted in the text colour).
    Drawn only: nothing is written."""
    win = view.window()
    prev = getattr(win, "_style_preview", None)
    if not prev or getattr(win, "view", None) is not view:
        return
    style, indices = prev
    doc = getattr(win, "doc", None)
    if doc is None or doc.page is None:
        return
    a = style.appearance or {}
    from ..style.theme import canvas_colour

    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    objs = doc.page.objects
    for i in indices:
        if not 0 <= i < len(objs):
            continue
        o = objs[i]
        kind = DS.kind_of_object(o)
        g = o.geometry
        box = QtCore.QRectF(g.x, g.y, g.extent_x, g.extent_y).normalized()
        if kind in TEXT_KINDS or (kind is None and style.family() == "text"):
            tc = a.get("text_colour")
            if tc is not None:
                painter.fillRect(box, _qc(tc, 90))
            # (a style without a text colour, or a black one, which would not show on a
            # dark page: the canvas's selection colour, the accent unless Settings > Canvas
            # chose another)
            pen = QtGui.QPen(_qc(tc, 220) if tc else canvas_colour("selection", 220), 0)
            pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(box)
            continue
        lw = max(1, int(a.get("line_width") or o.style.line_width or 1))
        line = a.get("line_colour")
        pen = QtGui.QPen(_qc(line) if line is not None else QtGui.QColor(QtCore.Qt.transparent), lw)
        pen.setStyle(_PEN_STYLES[int(a.get("line_style") or 0) % 5] if lw == 1 else QtCore.Qt.SolidLine)
        painter.setPen(pen)
        fill = a.get("fill_colour")
        closed = kind in ("rectangle", "ellipse", "polygon")
        if closed and a.get("filled") is not False and fill is not None:
            painter.setBrush(_qc(fill, 230))
        else:
            painter.setBrush(QtCore.Qt.NoBrush)
        nodes = list(getattr(g, "nodes", None) or ())
        if kind == "ellipse":
            painter.drawEllipse(box)
        elif kind in ("polygon", "line", "pipe") and len(nodes) >= 2:
            poly = QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in nodes])
            if kind == "polygon":
                painter.drawPolygon(poly)
            else:
                painter.drawPolyline(poly)
        else:
            r = float(a.get("corner_radius") or 0)
            painter.drawRoundedRect(box, r, r)


# --------------------------------------------------------------------------- the commands
class StyleCommands:
    """MainWindow's Edit > Styles commands (module doc)."""

    # -- the styles -----------------------------------------------------------------------
    def _styles(self) -> list:
        """The user's styles as dicts {name, appearance[, kind]}."""
        return [s.as_json() for s in store().user_styles()]

    def _put_styles(self, items: list) -> None:
        store().put_user_styles(_styles_from(items))
        self._rebuild_styles_menu()

    def _page_colour(self) -> int:
        doc = getattr(self, "doc", None)
        return DS.page_colour(doc.page if doc is not None else None)

    def _first_selected(self):
        """(index, kind) of the first selected object, else (None, None)."""
        if self.doc is None or not self._sel:
            return None, None
        i = self._sel[0]
        return i, DS.kind_of_object(self.doc.page.objects[i])

    def save_style(self, name: Optional[str] = None) -> None:
        """Styles > Save Appearance as Style…: the selected object's look under a name."""
        i, kind = self._first_selected()
        if i is None:
            return
        app = self.doc.appearance_of(i)
        if name is None:
            dlg = SaveStyleDialog(app, kind or "", self, self._page_colour())
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            name = dlg.name.text()
        name = (name or "").strip()
        if not name:
            return
        styles = [s for s in store().user_styles() if s.name.lower() != name.lower()]
        styles.append(Style(name, app, kind or ""))
        store().put_user_styles(sorted(styles, key=lambda s: s.name.lower()))
        self._rebuild_styles_menu()
        self.statusBar().showMessage("Saved the style %s (Edit > Styles applies it)." % name, 5000)

    def apply_style(self, name: str, shared: bool = False) -> None:
        """Give the selection the look of style ``name`` (one undo step)."""
        self.preview_style("")
        if self.doc is None or not self._sel:
            self.statusBar().showMessage("Select the objects to give the style %s." % name, 4000)
            return
        s = store().find(name, shared) or store().find(name)
        if s is None:
            return
        n = self.doc.apply_appearance(list(self._sel), s.appearance or {}, label="style %s" % name)
        self._refresh()
        self.statusBar().showMessage(
            "Applied the style %s to %d object%s." % (name, n, "" if n == 1 else "s"), 5000
        )

    def update_style(self, name: str) -> None:
        """Update Style from Selection: style ``name`` takes the selected object's look."""
        i, kind = self._first_selected()
        if i is None:
            return
        styles = store().user_styles()
        for s in styles:
            if s.name == name:
                s.appearance = self.doc.appearance_of(i)
                s.kind = kind or s.kind
                store().put_user_styles(styles)
                self._rebuild_styles_menu()
                self.statusBar().showMessage(
                    "The style %s now has the look of the selected object." % name, 5000
                )
                return

    def rename_style(self, name: str, new_name: Optional[str] = None) -> bool:
        """Rename Style: ``name`` becomes ``new_name`` (asked when None)."""
        styles = store().user_styles()
        s = next((x for x in styles if x.name == name), None)
        if s is None:
            return False
        if new_name is None:
            new_name, ok = QtWidgets.QInputDialog.getText(self, "Rename Style", "New name:", text=name)
            if not ok:
                return False
        new_name = (new_name or "").strip()
        if not new_name or new_name == name:
            return False
        if any(x.name.lower() == new_name.lower() for x in styles if x is not s):
            self.statusBar().showMessage("There is a style called %s already." % new_name, 5000)
            return False
        s.name = new_name
        store().put_user_styles(styles)
        self._rebuild_styles_menu()
        return True

    def select_by_style(self, name: str, shared: bool = False) -> int:
        """Select Objects with This Style: every top-level object whose look is style
        ``name``'s. How many were selected."""
        if self.doc is None:
            return 0
        s = store().find(name, shared) or store().find(name)
        if s is None:
            return 0
        doc = self.doc
        hits = []
        for i, o in enumerate(doc.page.objects):
            if o.parent is not None or o.raw.get("_from_template"):
                continue
            kind = DS.kind_of_object(o)
            if kind is None:
                continue
            try:
                if matches(s, doc.appearance_of(i), kind):
                    hits.append(i)
            except Exception:  # noqa: BLE001  # swallow-ok: an object without a look of its own
                continue
        if hits:
            # (as Select All selects: the panel and the tree follow)
            self._on_canvas_selection(hits)
            self.view.set_selection(hits)
        self.statusBar().showMessage(
            "%d object%s with the style %s." % (len(hits), "" if len(hits) == 1 else "s", name)
            if hits
            else "No object on the page has the style %s." % name,
            5000,
        )
        return len(hits)

    def set_style_as_default(self, name: str, kinds, shared: bool = False) -> None:
        """Set as Default for New Objects: the tools of ``kinds`` draw with style ``name``."""
        s = store().find(name, shared) or store().find(name)
        if s is None:
            return
        kinds = [k for k in kinds if k in DS.KIND_FIELDS]
        for k in kinds:
            DS.set_kind_style(k, s.appearance)
        self._after_tool_style_change()
        self.statusBar().showMessage(
            "New %s take the style %s." % (", ".join(DS.label(k, True).lower() for k in kinds), name), 5000
        )

    def style_selection_as_default(self) -> None:
        """Edit > Styles > Use Selection as Default for New <Kind>."""
        i, kind = self._first_selected()
        if i is None or kind is None:
            self.statusBar().showMessage("Select a line, shape, text or button first.", 4000)
            return
        DS.set_kind_style(kind, self.doc.appearance_of(i))
        self._after_tool_style_change()
        self.statusBar().showMessage(
            "New %s take the look of the selected one." % (DS.label(kind, True).lower()), 5000
        )

    def _after_tool_style_change(self) -> None:
        panel = getattr(self, "props", None)
        if panel is not None and hasattr(panel, "refresh_tool_style"):
            panel.refresh_tool_style()

    def manage_styles(self) -> None:
        """Styles > Manage Styles…: rename, remove, reorder, import and export."""
        dlg = ManageStylesDialog(self, self._page_colour())
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            store().put_user_styles(dlg.styles())
            self._rebuild_styles_menu()

    # -- the preview --------------------------------------------------------------------------
    def preview_style(self, name: str, shared: Optional[bool] = None) -> None:
        """Show the selection with style ``name`` on the canvas only ('' ends it; nothing
        shows while Settings turns the preview off: panel_options.STYLE_PREVIEW)."""
        if not _panels.STYLE_PREVIEW:
            name = ""
        s = store().find(name, shared) if name else None
        prev = (s, list(self._sel)) if s is not None and self.doc is not None and self._sel else None
        if prev is None and getattr(self, "_style_preview", None) is None:
            return
        self._style_preview = prev
        view = getattr(self, "view", None)
        if view is not None:
            try:
                view.request_overlay_update()
            except RuntimeError:  # swallow-ok: the canvas is being destroyed
                pass

    # -- the menu, the ribbon, the panel strip -------------------------------------------------
    def style_actions(self) -> dict:
        """{key: QAction}: one command per style ('shared:<name>' for a shared one); a click
        applies it, pointing at it in a menu previews it. Made again when the styles
        change (the menu, the ribbon gallery and the panel strip share them)."""
        acts = getattr(self, "_style_actions", None)
        from ..style.theme import ui_scale

        sig = tuple((s.name, s.shared, json.dumps(s.appearance, sort_keys=True)) for s in store().all()) + (
            self._page_colour(),
            ui_scale(),
        )
        if acts is not None and getattr(self, "_style_actions_sig", None) == sig:
            return acts
        for a in (acts or {}).values():
            try:
                a.deleteLater()
            except RuntimeError:  # swallow-ok: already gone with the window
                pass
        acts = {}
        page = self._page_colour()
        for s in store().all():
            a = QtGui.QAction(tile_icon(s, page), s.name, self)
            a.setToolTip(describe(s))
            a.setStatusTip("Give the selected objects the style %s." % s.name)
            a.triggered.connect(lambda _=False, n=s.name, sh=s.shared: self.apply_style(n, sh))
            a.hovered.connect(lambda n=s.name, sh=s.shared: self.preview_style(n, sh))
            acts[("shared:" if s.shared else "") + s.name] = a
        self._style_actions = acts
        self._style_actions_sig = sig
        return acts

    def style_menu_for(self, style: Style, menu: QtWidgets.QMenu) -> None:
        """A style's commands (a tile's right-click menu)."""
        from ..style.theme import icon

        has_sel = self.doc is not None and bool(self._sel)
        a = menu.addAction(icon("paste_appearance"), "Apply to the Selection")
        a.setEnabled(has_sel)
        a.triggered.connect(lambda: self.apply_style(style.name, style.shared))
        a = menu.addAction(icon("select_style"), "Select Objects with This Style")
        a.setEnabled(self.doc is not None)
        a.triggered.connect(lambda: self.select_by_style(style.name, style.shared))
        sub = menu.addMenu(icon("style_default"), "Set as Default for New Objects")
        self._fill_default_menu(sub, style)
        if not style.shared:
            menu.addSeparator()
            a = menu.addAction(icon("update_style"), "Update from Selection")
            a.setEnabled(has_sel)
            a.triggered.connect(lambda: self.update_style(style.name))
            a = menu.addAction(icon("rename_style"), "Rename…")
            a.triggered.connect(lambda: self.rename_style(style.name))
            a = menu.addAction(icon("delete"), "Remove")
            a.triggered.connect(lambda: self._remove_style(style.name))

    def _remove_style(self, name: str) -> None:
        store().put_user_styles([s for s in store().user_styles() if s.name != name])
        self._rebuild_styles_menu()

    def _fill_default_menu(self, menu: QtWidgets.QMenu, style: Style) -> None:
        kinds = style.kinds()
        a = menu.addAction(
            "All %s" % ("Texts and Buttons" if style.family() == "text" else "Lines and Shapes")
        )
        a.triggered.connect(lambda _=False, ks=kinds: self.set_style_as_default(style.name, ks, style.shared))
        menu.addSeparator()
        for k in kinds:
            a = menu.addAction("New %s" % DS.label(k, True))
            a.triggered.connect(
                lambda _=False, kk=k: self.set_style_as_default(style.name, [kk], style.shared)
            )

    def _sync_style_default_action(self) -> None:
        """'Use Selection as Default for New <Kind>' names the selected object's kind."""
        a = getattr(self, "act_style_default", None)
        if a is None:
            return
        _i, kind = self._first_selected() if getattr(self, "doc", None) is not None else (None, None)
        a.setText("Use Selection as Default for New %s" % (DS.label(kind, True) if kind else "Objects"))
        a.setEnabled(kind is not None)

    def _rebuild_styles_menu(self) -> None:
        m = getattr(self, "styles_menu", None)
        acts = self.style_actions()
        # (what the menu, the gallery and the panel showed last: the gallery may have made
        # the actions already, on the store's own change signal)
        before = getattr(self, "_styles_shown_sig", None)
        self._styles_shown_sig = getattr(self, "_style_actions_sig", None)
        renewed = before != self._styles_shown_sig
        if m is not None:
            from ..style.theme import icon

            m.clear()
            m.addAction(self.act_save_style)
            styles = store().all()
            has_sel = self.doc is not None and bool(self._sel)
            own = [s for s in styles if not s.shared]
            from ..main_window_base import SUBMENU_DESCRIPTIONS as _SUB

            sub = m.addMenu(icon("update_style"), "Update Style from Selection")
            sub.menuAction().setStatusTip(_SUB["Update Style from Selection"])
            for s in own:
                a = sub.addAction(tile_icon(s, self._page_colour()), s.name)
                a.triggered.connect(lambda _=False, n=s.name: self.update_style(n))
            sub.setEnabled(has_sel and bool(own))
            sub = m.addMenu(icon("rename_style"), "Rename Style")
            sub.menuAction().setStatusTip(_SUB["Rename Style"])
            for s in own:
                a = sub.addAction(tile_icon(s, self._page_colour()), s.name + "…")
                a.triggered.connect(lambda _=False, n=s.name: self.rename_style(n))
            sub.setEnabled(bool(own))
            sub = m.addMenu(icon("select_style"), "Select Objects with This Style")
            sub.menuAction().setStatusTip(_SUB["Select Objects with This Style"])
            for s in styles:
                a = sub.addAction(tile_icon(s, self._page_colour()), s.name)
                a.triggered.connect(lambda _=False, n=s.name, sh=s.shared: self.select_by_style(n, sh))
            sub.setEnabled(self.doc is not None and bool(styles))
            sub = m.addMenu(icon("style_default"), "Set as Default for New Objects")
            sub.menuAction().setStatusTip(_SUB["Set as Default for New Objects"])
            for s in styles:
                ss = sub.addMenu(tile_icon(s, self._page_colour()), s.name)
                self._fill_default_menu(ss, s)
            sub.setEnabled(bool(styles))
            if getattr(self, "act_style_default", None) is not None:
                self._sync_style_default_action()
                m.addAction(self.act_style_default)
            if own:
                m.addSeparator()
                for s in own:
                    a = acts.get(s.name)
                    if a is not None:
                        a.setEnabled(has_sel)
                        m.addAction(a)
            shared = [s for s in styles if s.shared]
            if shared:
                m.addSection("Shared: %s" % os.path.basename(store().shared_path()))
                for s in shared:
                    a = acts.get("shared:" + s.name)
                    if a is not None:
                        a.setEnabled(has_sel)
                        m.addAction(a)
            m.addSeparator()
            m.addAction(self.act_manage_styles)
            self.act_save_style.setEnabled(has_sel)
        gallery = getattr(self, "ribbon_styles", None)
        if gallery is not None and (renewed or not gallery.tiles):
            try:
                gallery.rebuild()
            except RuntimeError:  # swallow-ok: the gallery is being destroyed
                pass
        group = getattr(self, "_styles_group", None)
        if group is not None:
            # (a collapsed Styles group lists the commands and the styles in its menu)
            group.extras = [self.act_save_style, self.act_manage_styles] + list(acts.values())
            if renewed:
                group.contents_changed()
        panel = getattr(self, "props", None)
        if renewed and before is not None and panel is not None and getattr(panel, "_indices", None):
            # (the Appearance section's strip shows the styles as they are now)
            QtCore.QTimer.singleShot(0, panel, panel.refresh)

    def _styles_menu_hidden(self) -> None:
        self.preview_style("")

    def _install_styles(self) -> None:
        """The styles' wiring (MainWindow construction): the store's changes, the menu's
        preview, the canvas overlay, the selection naming the default command's kind."""
        from ..editor import canvas_hooks

        canvas_hooks.register_overlay("style_preview", paint_preview, "foreground", order=40)
        store().changed.connect(self._rebuild_styles_menu)
        m = getattr(self, "styles_menu", None)
        if m is not None:
            m.aboutToHide.connect(self._styles_menu_hidden)
        view = getattr(self, "view", None)
        if view is not None and hasattr(view, "selectionChanged"):
            view.selectionChanged.connect(self._styles_selection_changed)

    def _styles_selection_changed(self, *_a) -> None:
        self._sync_style_default_action()
        if getattr(self, "_style_preview", None) is not None:
            self.preview_style("")
