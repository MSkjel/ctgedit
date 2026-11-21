"""Text and combo-box controls on a simulated page."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6 import QtGui, QtWidgets

from ...render.scene import PAGE_INDEX_ROLE

_ACTIVEX = 0x0D
#: the controls drawn as a text field: OLE CLSID (upper case) -> ProgID
TEXT_CONTROLS = {
    "{C21CD4A3-BAA4-4473-8A16-8D22B2664E6B}": "ciText.TextBox",
    "{E8A1DF62-3D7C-49B6-9344-2104253063A4}": "ciText.ComboBox",
    "{8BD21D10-EC42-11CE-9E0D-00AA006002F3}": "Forms.TextBox.1",
}
#: above the page and what its Cicode drew (render/sim_drawn.Z_DRAWN = 100000)
Z_FIELDS = 300000.0
#: a dynamic property marking the proxies (SimCanvas hands them the mouse and keys)
FIELD_PROPERTY = "_ctgSimField"


def is_field(item) -> bool:
    """A control's field, or the drop-down list of a combo box (Qt shows it in a proxy of
    its own). Found through the widgets, never parentItem(): PySide6 hands a parent item to
    Python and deletes it (tests/test_code_contracts.py)."""
    if not isinstance(item, QtWidgets.QGraphicsProxyWidget):
        return False
    if item.property(FIELD_PROPERTY):
        return True
    w = item.widget()
    while w is not None:
        if w.property(FIELD_PROPERTY):
            return True
        w = w.parentWidget()
    return False


class _Field(QtWidgets.QLineEdit):
    """One control's text box (the runtime look of a Windows text box, whatever the theme)."""

    def __init__(self, an: int, name: str):
        super().__init__()
        self.an = an
        self.setAccessibleName("Control %s" % (name or "AN %d" % an))
        self.setFrame(False)
        # (a page colour, not a theme colour: '/*data*/' keeps the theme from recolouring it)
        self.setStyleSheet(
            "/*data*/ QLineEdit { background: #ffffff; color: #000000; "
            "border: 1px solid #7a7a7a; padding: 0px 2px; }"
        )


class ControlFields:
    """The text fields of one simulated page. ``send(an, property, value)`` = the simulation's
    set_object_property."""

    def __init__(
        self, scene: QtWidgets.QGraphicsScene, send: Optional[Callable[[int, str, str], object]] = None
    ):
        self._scene = scene
        self._send = send
        self._proxies: Dict[int, QtWidgets.QGraphicsProxyWidget] = {}
        self._fields: Dict[int, QtWidgets.QLineEdit | QtWidgets.QComboBox] = {}

    # -- building ----------------------------------------------------------------------
    def rebuild(self, page) -> List[int]:
        """Fields for the page's text box controls (the scene was built again: the old
        proxies went with its items: a new page, or the page read again, whose controls the
        engine makes again). Returns their ANs."""
        self.clear()
        for o in getattr(page, "objects", None) or ():
            raw = getattr(o, "raw", None) or {}
            if raw.get("_type_code") != _ACTIVEX:
                continue
            clsid = str(raw.get("_ole_clsid") or "").upper()
            if clsid not in TEXT_CONTROLS:
                continue
            an = getattr(o, "an", None)
            if not isinstance(an, int) or an <= 0 or an in self._proxies:
                continue
            g = o.geometry
            w, h = max(4, int(g.extent_x) + 1), max(4, int(g.extent_y) + 1)
            props = raw.get("_props") or {}
            name = str(props.get("ACTIVEXEXT_*NAME") or props.get("__AN_NAME__") or "")
            if TEXT_CONTROLS[clsid] == "ciText.ComboBox":
                f = QtWidgets.QComboBox()
                f.setEditable(True)
                f.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
                f.setCompleter(None)
                f.setAccessibleName("Control %s" % (name or "AN %d" % an))
                f.activated.connect(lambda index, an=an: self._edited(an, "ListIndex", str(index)))
                edit = f.lineEdit()
            else:
                f = edit = _Field(an, name)
            font = QtGui.QFont("Segoe UI")
            font.setPixelSize(max(8, min(16, h - 8)))
            f.setFont(font)
            f.setFixedSize(w, h)
            edit.textEdited.connect(lambda text, an=an: self._edited(an, "Text", text))
            proxy = QtWidgets.QGraphicsProxyWidget()
            proxy.setWidget(f)
            proxy.setPos(float(g.x), float(g.y))
            proxy.setZValue(Z_FIELDS)
            proxy.setData(PAGE_INDEX_ROLE, None)
            proxy.setData(0, None)
            proxy.setProperty(FIELD_PROPERTY, True)
            f.setProperty(FIELD_PROPERTY, True)  # (its drop-down list finds it: is_field)
            self._scene.addItem(proxy)
            self._proxies[an] = proxy
            self._fields[an] = f
        return list(self._fields)

    def clear(self) -> None:
        for proxy in self._proxies.values():
            try:
                if proxy.scene() is self._scene:
                    self._scene.removeItem(proxy)
            except RuntimeError:
                pass  # (scene.clear() deleted it already)
        self._proxies = {}
        self._fields = {}

    # -- the engine's controls ----------------------------------------------------------
    def apply(self, controls: Optional[Dict[int, dict]]) -> None:
        """PAGE.controls ({AN: {properties}}): a field shows the control's Text (not while the
        user types in it)."""
        if controls is None:
            return
        for an, e in controls.items():
            props = e.get("properties") if isinstance(e, dict) else None
            if not isinstance(props, dict):
                continue
            f = self._fields.get(an)
            values = {str(k).lower(): "" if v is None else str(v) for k, v in props.items()}
            text = values.get("text")
            if isinstance(f, QtWidgets.QComboBox):
                items = e.get("items")
                if isinstance(items, list):
                    if items != [f.itemText(i) for i in range(f.count())]:
                        f.clear()
                        f.addItems(items)
                    try:
                        index = int(values.get("listindex", "-1"))
                    except ValueError:
                        index = -1
                    if index != f.currentIndex():
                        f.setCurrentIndex(index)
                # (not while the user types in it, as a text field)
                typing = f.hasFocus() or (f.lineEdit() is not None and f.lineEdit().hasFocus())
                if text is not None and not typing and text != f.currentText():
                    f.setEditText(text)
            elif f is not None and text is not None and not f.hasFocus() and f.text() != text:
                f.setText(text)

    def _edited(self, an: int, prop: str, value: str) -> None:
        if self._send is not None:
            try:
                self._send(an, prop, value)
            except Exception:  # noqa: BLE001 -- the field keeps the text
                from ...infra import logsetup

                logsetup.note_exception("sim.controls.send")

    # -- tests / the view --------------------------------------------------------------
    def fields(self) -> Dict[int, QtWidgets.QLineEdit | QtWidgets.QComboBox]:
        return dict(self._fields)

    def proxies(self) -> Dict[int, QtWidgets.QGraphicsProxyWidget]:
        return dict(self._proxies)
