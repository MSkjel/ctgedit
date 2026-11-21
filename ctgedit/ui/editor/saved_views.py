"""Saved views (View > Saved Views)."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import hashlib
import json
import os

from PySide6 import QtCore, QtWidgets

#: at most this many views per page: the default of Settings > Canvas > Zoom and scroll >
#: Saved views a page keeps (ui/prefs/timing_options.SAVED_VIEWS_MAX)
MAX_VIEWS = 40


def page_name(doc):
    """What views are stored for: the page file (a library element: its library file +
    element name), or None."""
    path = getattr(doc, "path", None)
    if not path:
        return None
    name = os.path.normcase(os.path.abspath(path))
    element = getattr(doc, "element_name", None)
    if element:
        name += "|" + str(element).lower()
    return name


def views_key(doc):
    name = page_name(doc)
    if name is None:
        return None
    return "views/%s" % hashlib.sha1(name.encode("utf-8")).hexdigest()[:20]


def load_views(doc) -> list:
    """The saved views of ``doc``: [{name, zoom, cx, cy}], at most Settings > Canvas > Zoom
    and scroll > Saved views a page keeps (a lowered setting cuts a stored list at once)."""
    key = views_key(doc) if doc is not None else None
    if key is None:
        return []
    from ..prefs import timing_options
    from ..prefs.settings import app_settings

    raw = app_settings().value(key, "", type=str)
    try:
        data = json.loads(raw) if raw else {}
        out = []
        for v in data.get("views", []):
            out.append(
                {"name": str(v["name"]), "zoom": float(v["zoom"]), "cx": float(v["cx"]), "cy": float(v["cy"])}
            )
        return out[: timing_options.SAVED_VIEWS_MAX]
    except ValueError, TypeError, KeyError, AttributeError:  # swallow-ok: garbled = none
        return []


def store_views(doc, views: list) -> None:
    key = views_key(doc) if doc is not None else None
    if key is None:
        return
    from ..prefs import timing_options
    from ..prefs.settings import app_settings

    st = app_settings()
    views = list(views)[: timing_options.SAVED_VIEWS_MAX]
    if views:
        st.setValue(key, json.dumps({"page": page_name(doc), "views": views}))
    else:
        st.remove(key)


def full_text() -> str:
    """Why a new view is not saved: the page has as many as it keeps."""
    from ..prefs import timing_options

    return (
        "This page already has %d saved views, the most it keeps (Settings > Canvas > Zoom and "
        "scroll). Remove one, or save under the name of one to replace it." % timing_options.SAVED_VIEWS_MAX
    )


def is_full(views: list, name: str = "") -> bool:
    """A view named ``name`` cannot be added: the page has its most and none has that name."""
    from ..prefs import timing_options

    return len(views) >= timing_options.SAVED_VIEWS_MAX and name not in {v["name"] for v in views}


def current_view(view, name: str) -> dict:
    c = view.view_centre()
    return {"name": name, "zoom": round(view.zoom(), 6), "cx": round(c.x(), 2), "cy": round(c.y(), 2)}


def next_name(views: list) -> str:
    names = {v["name"] for v in views}
    k = len(views) + 1
    while "View %d" % k in names:
        k += 1
    return "View %d" % k


class SaveViewPopover(QtWidgets.QFrame):
    """Save Current View…: a small popover asking the view's name ('View n' to start)."""

    saved = QtCore.Signal(str)

    def __init__(self, default: str, parent=None):
        super().__init__(parent, QtCore.Qt.Popup)
        self.setObjectName("Popover")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(_sp(2), _sp(1), _sp(2), _sp(1))
        lay.addWidget(QtWidgets.QLabel("Name:"))
        self.field = QtWidgets.QLineEdit(default)
        self.field.setAccessibleName("Name of the view")
        self.field.setMinimumWidth(180)
        self.field.selectAll()
        lay.addWidget(self.field, 1)
        ok = QtWidgets.QPushButton("Save")
        ok.setDefault(True)
        ok.clicked.connect(self._save)
        self.field.returnPressed.connect(self._save)
        lay.addWidget(ok)
        from .navbar import surface_qss

        self.setStyleSheet(surface_qss("Popover"))

    def _save(self) -> None:
        name = self.field.text().strip()
        if name:
            self.saved.emit(name)
            self.close()

    def open_at(self, global_pos: QtCore.QPoint) -> None:
        self.adjustSize()
        self.move(global_pos)
        self.show()
        self.field.setFocus()


class ManageSavedViewsDialog(QtWidgets.QDialog):
    """View > Saved Views > Manage Saved Views…: the page's views to add, go to, rename,
    remove and reorder (modeless; it shows the page tab shown)."""

    def __init__(self, window):
        super().__init__(window)
        self._w = window
        self._doc = None
        lay = QtWidgets.QVBoxLayout(self)
        self._page = QtWidgets.QLabel("")
        lay.addWidget(self._page)
        row = QtWidgets.QHBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.setAccessibleName("Saved views")
        self.list.itemDoubleClicked.connect(lambda _it: self._go())
        self.list.itemChanged.connect(self._renamed)
        self.list.currentRowChanged.connect(lambda _r: self._sync_buttons())
        row.addWidget(self.list, 1)
        side = QtWidgets.QVBoxLayout()
        self.b_go = QtWidgets.QPushButton("Go To")
        self.b_go.clicked.connect(self._go)
        self.b_up = QtWidgets.QPushButton("Move Up")
        self.b_up.clicked.connect(lambda: self._move(-1))
        self.b_down = QtWidgets.QPushButton("Move Down")
        self.b_down.clicked.connect(lambda: self._move(1))
        for b in (self.b_go, self.b_up, self.b_down):
            side.addWidget(b)
        side.addStretch(1)
        row.addLayout(side)
        lay.addLayout(row, 1)
        edit = QtWidgets.QHBoxLayout()
        self.b_add = QtWidgets.QPushButton("Add")
        self.b_add.setToolTip("Save the view the canvas shows now")
        self.b_add.clicked.connect(self._add)
        self.b_rename = QtWidgets.QPushButton("Rename")
        self.b_rename.clicked.connect(self._rename)
        self.b_remove = QtWidgets.QPushButton("Remove")
        self.b_remove.clicked.connect(self._remove)
        for b in (self.b_add, self.b_rename, self.b_remove):
            edit.addWidget(b)
        edit.addStretch(1)
        lay.addLayout(edit)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.load_for(getattr(window, "doc", None))

    # ---- the page shown ---------------------------------------------------------------
    def load_for(self, doc) -> None:
        self._doc = doc
        name = ""
        if doc is not None:
            namer = getattr(self._w, "_doc_name", None)
            name = namer(doc) if callable(namer) else os.path.basename(doc.path)
        self._page.setText("Views of '%s'" % name if name else "No page is open.")
        self.setWindowTitle("Saved Views - %s" % name if name else "Saved Views - ctgedit")
        self.list.blockSignals(True)
        self.list.clear()
        for v in load_views(doc) if doc is not None else []:
            it = QtWidgets.QListWidgetItem(v["name"])
            it.setData(QtCore.Qt.UserRole, v)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsEditable)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self._sync_buttons()

    def views(self) -> list:
        out = []
        for k in range(self.list.count()):
            it = self.list.item(k)
            v = dict(it.data(QtCore.Qt.UserRole))
            v["name"] = it.text().strip() or v["name"]
            out.append(v)
        return out

    def _store(self) -> None:
        if self._doc is not None:
            store_views(self._doc, self.views())
            sync = getattr(self._w, "_sync_saved_views_menu", None)
            if callable(sync):
                sync(force=True)
        self._sync_buttons()  # (Add follows the count: off at the page's most)

    def _sync_buttons(self) -> None:
        row = self.list.currentRow()
        has = row >= 0
        full = is_full(self.views())
        self.b_add.setEnabled(
            self._doc is not None and self._doc is getattr(self._w, "doc", None) and not full
        )
        self.b_add.setToolTip(full_text() if full else "Save the view the canvas shows now")
        for b in (self.b_go, self.b_rename, self.b_remove):
            b.setEnabled(has)
        self.b_up.setEnabled(has and row > 0)
        self.b_down.setEnabled(has and row < self.list.count() - 1)

    # ---- the buttons ------------------------------------------------------------------
    def _add(self) -> None:
        if self._doc is None or self._doc is not getattr(self._w, "doc", None):
            return
        views = self.views()
        if is_full(views):
            return  # (Add is off: its tool tip says why)
        v = current_view(self._w.view, next_name(views))
        it = QtWidgets.QListWidgetItem(v["name"])
        it.setData(QtCore.Qt.UserRole, v)
        it.setFlags(it.flags() | QtCore.Qt.ItemIsEditable)
        self.list.blockSignals(True)
        self.list.addItem(it)
        self.list.blockSignals(False)
        self.list.setCurrentItem(it)
        self._store()
        self.list.editItem(it)

    def _go(self) -> None:
        it = self.list.currentItem()
        if it is None or self._doc is not getattr(self._w, "doc", None):
            return
        v = it.data(QtCore.Qt.UserRole)
        self._w.view.go_to_view(v["zoom"], v["cx"], v["cy"])

    def _rename(self) -> None:
        it = self.list.currentItem()
        if it is not None:
            self.list.editItem(it)

    def _renamed(self, it) -> None:
        if not it.text().strip():
            self.list.blockSignals(True)
            it.setText(it.data(QtCore.Qt.UserRole)["name"])
            self.list.blockSignals(False)
        self._store()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self._store()
            self._sync_buttons()

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        to = row + step
        if row < 0 or not 0 <= to < self.list.count():
            return
        self.list.blockSignals(True)
        it = self.list.takeItem(row)
        self.list.insertItem(to, it)
        self.list.blockSignals(False)
        self.list.setCurrentRow(to)
        self._store()
