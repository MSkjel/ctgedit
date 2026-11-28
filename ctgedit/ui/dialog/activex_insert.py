"""Edit > Insert ActiveX Control."""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

from ...infra.logsetup import note_exception as _note_exc

#: the factory kinds that are ActiveX controls (kind -> what the list calls it)
BUILT_IN = (
    ("activex", "Plant SCADA Meter"),
    ("process_analyst", "Process Analyst"),
    ("scheduler", "Scheduler"),
    ("db_exchange", "Database Exchange"),
    ("pelco", "Pelco Camera Viewer (deprecated)"),
)


def _kind_clsid(kind: str) -> str:
    try:
        from ...format import factory
        from ...io.pgregister import ole_clsid

        po = factory.create_object(kind, (0, 0, 100, 100))
        return ole_clsid(po.body_raw)
    except Exception:  # noqa: BLE001
        _note_exc("activex_insert._kind_clsid", kind=kind)
        return ""


class InsertActiveXDialog(QtWidgets.QDialog):
    """Pick the control the ActiveX tool places."""

    def __init__(self, project_dir: Optional[str], parent=None):
        super().__init__(parent)
        from ...format import factory
        from ...io import activex as AX

        self.setWindowTitle("Insert ActiveX Control")
        self.resize(640, 420)
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("Choose a control, then drag its box on the page.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        from ..style.theme import SearchField

        self.search = SearchField(
            "Search controls", tip="Matches the control name, ProgID and where it comes from"
        )
        self.search.searchChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Control", "ProgID", "From"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.itemDoubleClicked.connect(lambda _it, _c: self._accept())
        self.tree.currentItemChanged.connect(lambda cur, _p: self._show(cur))
        lay.addWidget(self.tree, 1)
        self.search.attach(self.tree, self)
        self.location = QtWidgets.QLabel()
        self.location.setWordWrap(True)
        self.location.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.location)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.insert_btn = self.buttons.addButton("Insert", QtWidgets.QDialogButtonBox.AcceptRole)
        self.insert_btn.setDefault(True)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._choice = None
        have = set(factory.available_kinds())
        seen = set()
        from ..busy import wait_cursor

        with wait_cursor():
            for kind, label in BUILT_IN:
                if kind not in have:
                    continue
                clsid = _kind_clsid(kind)
                seen.add(clsid)
                info = AX.control_info(clsid) if clsid else None
                self._add(
                    label if info is None else "%s (%s)" % (label, info.name),
                    info,
                    "ctgedit",
                    ("kind", kind),
                    clsid,
                )
            try:
                donors = AX.project_controls(project_dir) if project_dir else ()
            except Exception:  # noqa: BLE001 -- the list is an aid
                _note_exc("activex_insert.project_controls", project=project_dir)
                donors = ()
            for d in donors:
                if d.clsid in seen:
                    continue
                seen.add(d.clsid)
                info = AX.control_info(d.clsid)
                name = info.name if info is not None else (d.name or d.clsid)
                self._add(name, info, "page %s" % d.page, ("donor", d), d.clsid)
        from ..tables import make_resizable

        make_resizable(self.tree, "InsertActiveXDialog/controls")
        if self.tree.topLevelItemCount():
            # the control inserted last, else the first
            from ..prefs.settings import app_settings

            last = str(app_settings().value("edit/last_activex", "") or "").upper()
            pick = self.tree.topLevelItem(0)
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if last and str(it.data(0, QtCore.Qt.UserRole)[2] or "").upper() == last:
                    pick = it
                    break
            self.tree.setCurrentItem(pick)
        self.insert_btn.setEnabled(self.tree.topLevelItemCount() > 0)
        self.search.setFocus()

    def _add(self, name, info, source, choice, clsid) -> None:
        it = QtWidgets.QTreeWidgetItem([name, info.progid if info is not None else "", source])
        it.setData(0, QtCore.Qt.UserRole, (choice, info, clsid, name))
        if info is None:
            it.setToolTip(0, "Not registered on this computer")
            it.setText(1, "(not registered on this computer)")
        self.tree.addTopLevelItem(it)

    def _filter(self, text: str) -> None:
        m = self.search.matcher(text)
        first = None
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            hit = m(*(it.text(c) for c in range(3)))
            it.setHidden(not hit)
            if hit and first is None:
                first = it
        if first is not None:
            self.tree.setCurrentItem(first)
        self.insert_btn.setEnabled(first is not None)

    def _show(self, it) -> None:
        if it is None:
            self.location.setText("")
            return
        _choice, info, clsid, _name = it.data(0, QtCore.Qt.UserRole)
        if info is not None:
            self.location.setText("Location: %s\nCLSID: %s" % (info.server or "?", clsid))
        else:
            self.location.setText("CLSID: %s (not registered on this computer)" % clsid)

    def select(self, text: str) -> bool:
        """Filter to ``text`` and select the first match (tests / scripting)."""
        self.search.setText(text)
        self.search.flush()
        return self.tree.currentItem() is not None and not self.tree.currentItem().isHidden()

    def _accept(self) -> None:
        it = self.tree.currentItem()
        if it is None or it.isHidden():
            return
        self._choice = it.data(0, QtCore.Qt.UserRole)
        from ..prefs.settings import app_settings

        app_settings().setValue("edit/last_activex", str(self._choice[2] or ""))
        self.accept()

    def choice(self):
        """('kind', factory kind) or ('donor', io.activex.ControlDonor)."""
        return self._choice[0] if self._choice else None

    def choice_label(self) -> str:
        return self._choice[3] if self._choice else ""
