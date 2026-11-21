"""File > Page History."""

from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...infra import history as H
from ..style.theme import set_note_role

HELP_TOPIC = "opening-and-saving#earlier-versions-of-a-page"
VERSIONS_KEY = "opt/history_versions"
MB_KEY = "opt/history_mb"
#: the least time between two earlier versions (minutes; 0 = every
#: save)
EVERY_KEY = "opt/history_every_min"


def is_version(doc) -> bool:
    """``doc`` is an earlier version shown read-only (Open Read-only): nothing writes it in
    place, Save goes to Save As."""
    return getattr(getattr(doc, "ui", None), "version_view", None) is not None


#: what a save of a version tab says
VERSION_SAVE_TEXT = (
    "This tab shows an earlier version of %s. Save As keeps it as a page of "
    "its own; File > Page History > Restore puts it back on the page."
)


def configure_from_settings() -> None:
    """Settings > Editing > Safety -> infra/history (at start and when they change)."""
    from ..prefs.settings import app_settings

    st = app_settings()
    try:
        versions = int(str(st.value(VERSIONS_KEY, 10)).strip())
    except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
        versions = 10
    try:
        mb = int(str(st.value(MB_KEY, 200)).strip())
    except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
        mb = 200
    from ..prefs.settings import HISTORY_EVERY_RANGE, whole

    H.configure(versions, mb, whole(EVERY_KEY, HISTORY_EVERY_RANGE))


def _apply_settings(_window) -> None:
    """Settings applied (live, at each step of a spin box): the new numbers only. Fewer
    versions or less room take effect at each page's next save (keep_version prunes
    then): stepping the box down and up again removes nothing."""
    configure_from_settings()


def _register_settings_hooks() -> None:
    from ..prefs import settings_pages as SP

    for key in (VERSIONS_KEY, MB_KEY, EVERY_KEY):
        if _apply_settings not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, _apply_settings)


def _when(t: float) -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


def size_text(n: int) -> str:
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / 1048576.0)
    return "%d KB" % max(1, round(n / 1024.0)) if n else "0 KB"


def disk_use_text() -> str:
    """'12.3 MB in 30 versions' (Settings > Editing > Safety, Page History)."""
    total, count = H.disk_use()
    if not count:
        return "No earlier versions are kept now."
    return "%s in %d version%s." % (size_text(total), count, "" if count == 1 else "s")


def clear_all_versions(parent) -> bool:
    """Clear… (Settings > Editing > Safety, Page History): asks, then removes the earlier
    versions of every page. True when they went."""
    n = H.disk_use()[1]
    if not n:
        return False
    from ..messages import confirm

    if not confirm(
        parent,
        "Clear Earlier Versions",
        "Remove the %d earlier version%s of every page? File > Page History then lists "
        "none until the next save." % (n, "" if n == 1 else "s"),
        "Remove Versions",
    ):
        return False
    H.clear()
    return True


def _doc_source(doc):
    """(path, element) the versions of ``doc`` are kept under."""
    if hasattr(doc, "element_name") and hasattr(doc, "session"):
        return doc.path, doc.element_name
    return doc.path, ""


def _thumbnail(v: H.Version) -> QtGui.QImage:
    from ...format import pagesummary as PS
    from ..browser.thumbs import bmp_to_qimage

    try:
        if v.element:
            s = PS.read_element_summary(v.file, v.element)
            bmp = s.thumb_bmp if s is not None else b""
        else:
            bmp = PS.read_page_summary(v.file, template_scan=0).thumb_bmp
    except Exception:  # noqa: BLE001  # swallow-ok: a version without a picture
        bmp = b""
    return bmp_to_qimage(bmp)


class PageHistoryDialog(QtWidgets.QDialog):
    """File > Page History (modeless, page scope): the earlier versions of one page."""

    #: (what, version): 'open' | 'compare' | 'restore' | 'underlay'
    versionRequested = QtCore.Signal(str, object)

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.path, self.element = _doc_source(doc)
        name = (
            doc.display_name
            if self.element and hasattr(doc, "display_name")
            else os.path.splitext(os.path.basename(self.path))[0]
        )
        self.setWindowTitle("Page History - %s" % name)
        self.name = name
        lay = QtWidgets.QVBoxLayout(self)
        self.intro = QtWidgets.QLabel("")
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)
        # (where the versions are and how to turn them on, from here)
        self.btn_settings = None
        split = QtWidgets.QHBoxLayout()
        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setAccessibleName("Earlier versions")
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Saved", "Objects", "Size"])
        from ..browser.thumbs import ROW_SIZE

        self.tree.setIconSize(ROW_SIZE)
        from ..tables import make_resizable

        make_resizable(self.tree, "PageHistoryDialog/versions", stretch=0)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.itemActivated.connect(lambda *_a: self._request("open"))
        split.addWidget(self.tree, 1)
        side = QtWidgets.QVBoxLayout()
        from ..browser.thumbs import PREVIEW_SIZE

        self.preview = QtWidgets.QLabel(self)
        self.preview.setFixedSize(PREVIEW_SIZE)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setAccessibleName("Preview")
        side.addWidget(self.preview)
        self.detail = QtWidgets.QLabel("")
        self.detail.setWordWrap(True)
        set_note_role(self.detail, "text_dim")
        self.detail.setMaximumWidth(PREVIEW_SIZE.width())
        side.addWidget(self.detail)
        side.addStretch(1)
        split.addLayout(side)
        lay.addLayout(split, 1)
        bb = QtWidgets.QDialogButtonBox(self)
        self.btn_open = bb.addButton("Open Read-only", QtWidgets.QDialogButtonBox.ActionRole)
        self.btn_open.setToolTip("Show this version in a tab of its own, read-only")
        self.btn_compare = bb.addButton("Compare", QtWidgets.QDialogButtonBox.ActionRole)
        self.btn_compare.setToolTip("Mark what differs between the page and this version")
        self.btn_underlay = bb.addButton("Underlay", QtWidgets.QDialogButtonBox.ActionRole)
        self.btn_underlay.setToolTip(
            "Draw this version faded on the canvas, under the page as it is now (View > Underlay)"
        )
        self.btn_restore = bb.addButton("Restore…", QtWidgets.QDialogButtonBox.ActionRole)
        self.btn_restore.setToolTip("Put this version on the page as unsaved changes")
        self.btn_folder = bb.addButton("Open Folder", QtWidgets.QDialogButtonBox.ActionRole)
        self.btn_folder.setToolTip("Show the folder the earlier versions of this page are kept in")
        self.btn_settings = bb.addButton("Settings…", QtWidgets.QDialogButtonBox.ActionRole)
        self.btn_settings.setToolTip(
            "How many earlier versions each page keeps, and how "
            "much room they take (Settings > Editing > Safety)"
        )
        bb.addButton(QtWidgets.QDialogButtonBox.Close)
        self.btn_open.clicked.connect(lambda: self._request("open"))
        self.btn_compare.clicked.connect(lambda: self._request("compare"))
        self.btn_underlay.clicked.connect(lambda: self._request("underlay"))
        self.btn_restore.clicked.connect(lambda: self._request("restore"))
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_settings.clicked.connect(self.open_settings)
        bb.rejected.connect(self.reject)
        # the room the versions of every page take; Clear… here removes this page's only
        # (every page's: Settings > Editing > Safety)
        use = QtWidgets.QHBoxLayout()
        self.use_label = QtWidgets.QLabel("")
        set_note_role(self.use_label, "text_dim")
        use.addWidget(self.use_label, 1)
        self.btn_clear = QtWidgets.QPushButton("Clear This Page's Versions…")
        self.btn_clear.setAutoDefault(False)
        self.btn_clear.setToolTip(
            "Remove the earlier versions of this page. Settings > Editing > Safety clears every page's"
        )
        self.btn_clear.clicked.connect(self.clear_page)
        use.addWidget(self.btn_clear)
        lay.addLayout(use)
        lay.addWidget(bb)
        self.setMinimumSize(640, 320)
        self.resize(780, 420)
        self.versions = []
        self.reload()

    # -- the list ----------------------------------------------------------------------
    def reload(self) -> None:
        """Read the versions again (the page was saved, or a setting changed); keeps the
        version picked when it is still there."""
        keep = self.current()
        self.versions = H.list_versions(self.path, self.element)
        self.tree.clear()
        for v in self.versions:
            it = QtWidgets.QTreeWidgetItem([v.when, str(v.objects) if v.objects else "", size_text(v.size)])
            img = _thumbnail(v)
            if not img.isNull():
                from ..browser.thumbs import ROW_SIZE, fit_image

                it.setIcon(0, QtGui.QIcon(QtGui.QPixmap.fromImage(fit_image(img, ROW_SIZE))))
            it.setToolTip(0, "Saved at %s. A save replaced it at %s" % (v.when, _when(v.replaced_at)))
            it.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            it.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.tree.addTopLevelItem(it)
        if not H.enabled():
            text = (
                ("Earlier versions are off (Settings… turns them on). The versions kept before are listed.")
                if self.versions
                else "Earlier versions are off. Settings… turns them on."
            )
        elif not self.versions:
            text = "No earlier versions yet. Each save keeps the page as it was before it."
        else:
            text = "The versions of %s saved before, newest first." % self.name
        self.intro.setText(text)
        self.use_label.setText("All pages: %s They are kept in %s." % (disk_use_text(), H.history_root()))
        self.btn_clear.setEnabled(bool(self.versions))
        k = 0
        if keep is not None:
            k = next((i for i, v in enumerate(self.versions) if v.file == keep.file), 0)
        if self.versions:
            self.tree.setCurrentItem(self.tree.topLevelItem(k))
        self._current_changed()

    def versions_folder(self) -> str:
        """The folder this page's earlier versions are kept in (under history_root())."""
        return os.path.join(H.history_root(), H.key_for(self.path, self.element))

    def open_folder(self) -> None:
        from ..messages import open_folder

        folder = self.versions_folder()
        open_folder(folder if os.path.isdir(folder) else H.history_root())

    def open_settings(self) -> None:
        """Settings > Editing > Safety, on 'Keep earlier versions of each page'."""
        from .palette import show_setting

        show_setting(
            self.parentWidget() or self,
            "Editing",
            "Keep earlier versions of each page",
            key="opt/history_versions",
        )
        self.reload()

    def clear_page(self) -> None:
        """Clear This Page's Versions…: asks (naming the page), then removes them."""
        n = len(self.versions)
        if not n:
            return
        from ..messages import confirm

        if confirm(
            self,
            "Clear Earlier Versions",
            "Remove the %d earlier version%s of %s? Other pages keep theirs."
            % (n, "" if n == 1 else "s", self.name),
            "Remove Versions",
        ):
            H.clear_page(self.path, self.element)
            self.reload()

    def current(self) -> Optional[H.Version]:
        it = self.tree.currentItem()
        k = self.tree.indexOfTopLevelItem(it) if it is not None else -1
        return self.versions[k] if 0 <= k < len(self.versions) else None

    def _current_changed(self, *_a) -> None:
        v = self.current()
        for b in (self.btn_open, self.btn_compare, self.btn_restore):
            b.setEnabled(v is not None)
        # (a library element's version is no page: it cannot be underlaid)
        self.btn_underlay.setEnabled(v is not None and not self.element)
        if v is None:
            self.preview.clear()
            self.preview.setText("")
            self.detail.setText("")
            return
        img = _thumbnail(v)
        if img.isNull():
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText("No picture")
        else:
            from ..browser.thumbs import PREVIEW_SIZE, fit_image

            self.preview.setPixmap(QtGui.QPixmap.fromImage(fit_image(img, PREVIEW_SIZE)))
        self.detail.setText(
            "%d objects, %s." % (v.objects, size_text(v.size)) if v.objects else "%s." % size_text(v.size)
        )

    def _request(self, what: str) -> None:
        v = self.current()
        if v is not None:
            self.versionRequested.emit(what, v)

    def changeEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().changeEvent(e)
        if e.type() == QtCore.QEvent.ActivationChange and self.isActiveWindow():
            # (a save since: its version is listed)
            if [v.file for v in H.list_versions(self.path, self.element)] != [v.file for v in self.versions]:
                self.reload()


# --------------------------------------------------------------------------- the window side
def version_document(doc, v: H.Version):
    """A document of version ``v`` of ``doc``'s page (element), drawn with the page's
    project: the page's path, the version's records. Nothing is read from or written to the
    page's file here."""
    data = H.read_bytes(v)
    if v.element:
        from ...editing import ElementDocument
        from ...format.element_session import open_element_session

        new = ElementDocument.open_element(doc.path, doc.element_name)
        sess, parsed = open_element_session(v.file, v.element)
        new.parsed = parsed
        # (the version's own hot spot: the session is the live element's, so a Restore
        # would keep today's)
        origin = tuple(getattr(sess, "origin", None) or ())
        if len(origin) == 2 and new.can_set_hotspot and tuple(new.hotspot or ()) != origin:
            new._hotspot = (int(origin[0]), int(origin[1]))
        new._rerender_page()
        return new
    from ...editing import EditableDocument

    return EditableDocument.from_bytes(data, doc.path)


class VersionCommands:
    """The editor window's side (a MainWindow mixin): File > Page History."""

    def _install_versions_ui(self) -> None:
        configure_from_settings()
        _register_settings_hooks()

    def page_history(self) -> None:
        """File > Page History…: the earlier versions of the page shown (modeless)."""
        self._show_target_tab()
        doc = self.doc
        if doc is None:
            return
        from ..modeless import doc_key

        if self._raise_modeless(self._modeless_for("pagehistory")):
            return
        dlg = PageHistoryDialog(doc, self)
        dlg.versionRequested.connect(self._version_requested)
        self._show_modeless(dlg, ("pagehistory", doc_key(doc)), scope="page", title="Page History")

    def _version_requested(self, what: str, v) -> None:
        dlg = self.sender()
        doc = getattr(dlg, "doc", None) or self.doc
        if doc is None:
            return
        if doc is not self.doc:
            self.set_document(doc)
        if what == "open":
            self.open_version_read_only(v)
        elif what == "compare":
            self.compare_with_version(v)
        elif what == "restore":
            self.restore_version(v, ask=True)
        elif what == "underlay":
            # (View > Underlay: the version drawn faded under the page: ui/editor/underlay.py)
            self.underlay_version_file(v)

    def open_version_read_only(self, v) -> None:
        """An earlier version in a tab of its own, read-only."""
        doc = self.doc
        if doc is None:
            return
        for e in self._docs:
            if (
                getattr(getattr(e["doc"], "ui", None), "version_view", None) is not None
                and e["doc"].ui.version_view.file == v.file
            ):
                self.set_document(e["doc"])
                return
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                new = version_document(doc, v)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        except Exception as e:  # noqa: BLE001 -- say why
            from ...infra import logsetup

            logsetup.note_exception("history_versions.open", page=doc.path)
            QtWidgets.QMessageBox.warning(self, "Page History", "The version could not be opened (%s)." % e)
            return
        new.read_only = True
        new._disk_stamp = None  # (the file on disk is the page's, not this one's)
        new.ui.version_view = v
        self.set_document(new)
        self.statusBar().showMessage(
            "The version of %s, read-only. Copy objects from it, or Restore it from File > "
            "Page History." % v.when,
            8000,
        )

    def restore_version(self, v, ask: bool = True) -> bool:
        """Put version ``v`` on the page shown as unsaved changes (the tab's document is the
        version now, unsaved; its undo history starts again). True when it was put back."""
        doc = self.doc
        if doc is None:
            return False
        if getattr(getattr(doc, "ui", None), "version_view", None) is not None:
            self.statusBar().showMessage("Restore a version on the page's own tab.", 5000)
            return False
        name = os.path.basename(doc.path)
        if ask:
            more = " Your unsaved changes are replaced too." if doc.dirty else ""
            if not self._confirm(
                "Restore Version",
                "Put the version of %s back on %s?%s" % (v.when, name, more),
                "Restore",
                "The page shows it as unsaved changes; save to keep it. The page's undo "
                "history starts again.",
            ):
                return False
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                new = version_document(doc, v)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        except Exception as e:  # noqa: BLE001 -- say why
            from ...infra import logsetup

            logsetup.note_exception("history_versions.restore", page=doc.path)
            QtWidgets.QMessageBox.warning(
                self, "Restore Version", "The version could not be restored (%s)." % e
            )
            return False
        new.read_only = doc.read_only
        new._disk_stamp = getattr(doc, "_disk_stamp", None)
        with_ctf = getattr(new, "_start_ctf_precheck", None)
        if with_ctf is not None and not v.element:
            try:
                with_ctf()
            except Exception:  # noqa: BLE001 -- the save checks the fast display itself then
                from ...infra import logsetup

                logsetup.note_exception("history_versions.restore/ctf", page=doc.path)
        new.dirty = True  # (an unsaved change no undo step holds)
        new.restored_version = v
        self._replace_tab_document(doc, new)
        from ...infra import logsetup

        logsetup.get("open").info("restored %s from its version of %s (%s)", doc.path, v.when, v.file)
        self.statusBar().showMessage(
            "Restored the version of %s as unsaved changes. Save to keep it." % v.when, 8000
        )
        return True

    def _replace_tab_document(self, old, new) -> None:
        """``new`` takes ``old``'s tab (as Revert to Saved does)."""
        k = next((i for i, e in enumerate(self._docs) if e["doc"] is old), None)
        if k is None:
            self.set_document(new)
            return
        from ..recovery import AutosaveController

        AutosaveController.forget(old)
        if old is self.doc:
            self._stash_current()
        self._close_page_windows(old)
        entry = self._docs[k]
        entry["doc"] = new
        entry["sel"] = []
        entry.pop("hidden", None)
        entry.pop("locked", None)
        if old is self.doc:
            self._activate_doc(entry)
        self._update_title()
        self._update_actions()
