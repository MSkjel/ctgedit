"""Tools > Pictures > Find Duplicate Pictures."""

from __future__ import annotations

import base64
import contextlib
import glob
import os
import re
from typing import Callable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..style import theme
from .picture_import import size_text

#: a library element name (Cicode-safe, the Graphics Builder's 31 characters)
_ELEMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,30}$")


def _thumb_icon(t: dict) -> QtGui.QIcon:
    try:
        raw = base64.b64decode(t["rgba"])
        img = QtGui.QImage(raw, t["w"], t["h"], t["w"] * 4, QtGui.QImage.Format_RGBA8888).copy()
    except Exception:  # noqa: BLE001 -- swallow-ok: a row without a thumbnail
        return QtGui.QIcon()
    canvas = QtGui.QPixmap(48, 48)
    canvas.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(canvas)
    scaled = img.scaled(48, 48, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    p.drawImage((48 - scaled.width()) // 2, (48 - scaled.height()) // 2, scaled)
    p.end()
    return QtGui.QIcon(canvas)


class _SymbolNameDialog(QtWidgets.QDialog):
    """The library and the symbol name for Make Library Symbol."""

    def __init__(self, libraries: List[str], element: str, project_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Make Library Symbol")
        self._dir = project_dir
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("The library of this project the picture goes into."))
        form = QtWidgets.QFormLayout()
        self.library = QtWidgets.QComboBox()
        self.library.setEditable(True)
        self.library.addItems(libraries or ["pictures"])
        self.library.setCurrentText("pictures" if "pictures" in libraries or not libraries else libraries[0])
        self.library.setToolTip("A symbol library (.ctl) of this project; a new name makes a new library")
        self.element = QtWidgets.QLineEdit(element)
        self.element.setToolTip("Letters, digits and _, at most 31 characters")
        form.addRow("Library:", self.library)
        form.addRow("Symbol:", self.element)
        lay.addLayout(form)
        self.problem = theme.note_label("", "danger", self)
        self.problem.setVisible(False)
        lay.addWidget(self.problem)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Make Symbol")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.library.currentTextChanged.connect(lambda _t: self._check())
        self.element.textChanged.connect(lambda _t: self._check())
        self._check()

    def names(self):
        return self.library.currentText().strip(), self.element.text().strip()

    def exists(self) -> bool:
        lib, el = self.names()
        path = os.path.join(self._dir, lib + ".ctl")
        if not os.path.exists(path):
            return False
        try:
            from ...format.symbols import open_library

            return open_library(path).element(el) is not None
        except Exception:  # noqa: BLE001 -- swallow-ok: an unreadable library has no such symbol
            return False

    def _check(self) -> None:
        lib, el = self.names()
        why = ""
        if not _ELEMENT.match(lib):
            why = "The library name can have letters, digits and _ (at most 31)."
        elif not _ELEMENT.match(el):
            why = "The symbol name can have letters, digits and _ (at most 31)."
        self.problem.setText(why)
        self.problem.setVisible(bool(why))
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(not why)


def locate_use(objs, digest: str, use: dict):
    """The record of ``objs`` (the page as it is open now) that holds the scan's occurrence
    ``use`` of picture ``digest``: the same picture at the same place. Its stored index
    first, then any record that matches (an object added or deleted above it moves it);
    None when none does."""
    from ...format import bitmaps
    from ...format import geometry as G

    def same(k: int) -> bool:
        po = objs[k]
        if po.type_code != bitmaps.BITMAP_TC:
            return False
        try:
            if bitmaps.picture_digest(po) != digest:
                return False
            return list(G.parse_geom_header(po.body_raw).rect1) == list(use["rect"])
        except Exception:  # noqa: BLE001  # swallow-ok: a record that does not read is not it
            return False

    k = int(use.get("index", -1))
    if 0 <= k < len(objs) and same(k):
        return k
    return next((j for j in range(len(objs)) if same(j)), None)


class DuplicatePicturesDialog(QtWidgets.QDialog):
    """The duplicate pictures of the project ``project_dir`` (``window`` = the editor)."""

    RESIZE = "both"

    def __init__(self, window, project_dir: str):
        super().__init__(window)
        self._w = window
        self.project_dir = project_dir
        self.project = os.path.basename(project_dir)
        self.setWindowTitle("Find Duplicate Pictures - %s" % self.project)
        self._groups: List[dict] = []
        self._job = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("The pictures the pages of %s store more than once." % self.project))
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.groups = QtWidgets.QTreeWidget()
        self.groups.setRootIsDecorated(False)
        self.groups.setIconSize(QtCore.QSize(48, 48))
        self.groups.setHeaderLabels(["Picture", "Pixels", "Copies", "Pages", "Each", "Could save"])
        self.groups.setUniformRowHeights(True)
        split.addWidget(self.groups)
        self.uses = QtWidgets.QTreeWidget()
        self.uses.setRootIsDecorated(False)
        self.uses.setHeaderLabels(["Page", "Position", "Size on the page"])
        self.uses.setToolTip("Double-click a copy to show it on its page")
        split.addWidget(self.uses)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)
        from ..tables import make_resizable

        make_resizable(self.groups, "DuplicatePicturesDialog/groups", stretch=0)
        make_resizable(self.uses, "DuplicatePicturesDialog/uses", stretch=0)
        row = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        # (a failure's status line has Open Log beside it, never 'see the log')
        from ..messages import open_log_button

        self.b_log = open_log_button(self)
        row.addWidget(self.b_log)
        self.cancel_job = QtWidgets.QToolButton()
        self.cancel_job.setText("Stop")
        self.cancel_job.setToolTip("Stop after the page being read or changed now")
        self.cancel_job.setVisible(False)
        row.addWidget(self.cancel_job)
        lay.addLayout(row)
        acts = QtWidgets.QHBoxLayout()
        # (its scope in its name, as Replace Everywhere's: it changes every page with a copy)
        self.b_symbol = QtWidgets.QPushButton("Make Symbol and Link Everywhere…")
        self.b_symbol.setToolTip(
            "Store the picture once, as a library symbol, and link every copy on every page to it"
        )
        self.b_replace = QtWidgets.QPushButton("Replace Everywhere…")
        self.b_replace.setToolTip("Put another picture file in the place of every copy")
        self.b_show = QtWidgets.QPushButton("Show")
        self.b_show.setToolTip("Open the copy's page and select it")
        self.b_scan = QtWidgets.QPushButton("Scan Again")
        for b in (self.b_symbol, self.b_replace, self.b_show, self.b_scan):
            b.setAutoDefault(False)
            acts.addWidget(b)
        acts.addStretch(1)
        lay.addLayout(acts)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.groups.currentItemChanged.connect(lambda *_a: self._show_uses())
        self.uses.itemDoubleClicked.connect(lambda *_a: self._show_copy())
        self.b_show.clicked.connect(self._show_copy)
        self.b_scan.clicked.connect(self.scan)
        self.b_symbol.clicked.connect(self._make_symbol)
        self.b_replace.clicked.connect(self._replace_everywhere)
        self.cancel_job.clicked.connect(self._stop)
        self._sync_buttons()
        self.resize(760, 560)
        QtCore.QTimer.singleShot(0, self, self.scan)

    # -- the helper process ------------------------------------------------------------
    def _run(self, req: dict, label: str, done: Callable[[dict], None]) -> None:
        from ...workers import picture_worker as PW
        from ...infra.paths import data_dir
        from ..jobs.helper_job import HelperJob

        job = HelperJob(
            self,
            PW.command,
            dict(req, format=PW.FORMAT),
            data_dir("pictures"),
            skip_paths=self._open_paths,
            label=label,
        )
        # (the figures of the run: the time so far, the estimate and the rate, ui/jobs/run_stats)
        from ..jobs.run_stats import RunStats

        stats = RunStats.from_settings()
        stats.begin_phase("pictures", 0)

        def progress(i, n, name):
            if not job.stopping:
                stats.update(int(i), int(n))
                figures = stats.time_text()
                text = "%s: page %d of %d (%s)" % (label, i + 1, n, name)
                if figures:
                    text += ". %s%s." % (figures[:1].upper(), figures[1:])
                self.status.setText(text)

        def finished(res):
            self._job = None
            self.cancel_job.setVisible(False)
            if res.get("error"):
                self.status.setText("%s failed: %s" % (label, res["error"]))
            else:
                done(res)
            self._sync_buttons()

        job.progress.connect(progress)
        job.finished.connect(finished)
        if not job.start():
            self.status.setText("The pictures could not be read.")
            self.b_log.setVisible(True)
            return
        self._job = job
        self.cancel_job.setVisible(True)
        self.cancel_job.setEnabled(True)
        self.b_log.setVisible(False)
        self.status.setText(label + "…")
        self._sync_buttons()

    def _stop(self) -> None:
        if self._job is not None:
            self._job.stop()
            self.cancel_job.setEnabled(False)
            self.status.setText("Stopping after the page being read or changed now…")

    def busy(self) -> bool:
        return self._job is not None

    def reject(self) -> None:
        if self._job is not None:
            self._job.stop()
            self._job.wait(30)
        super().reject()

    def _open_docs(self) -> list:
        """The pages of this project open in an editor window."""
        from ..jobs.helper_job import open_documents

        return open_documents([self.project_dir])

    def _open_paths(self) -> List[str]:
        return [d.path for _w, d in self._open_docs()]

    # -- the scan ----------------------------------------------------------------------
    def scan(self) -> None:
        if self._job is not None:
            return
        self.groups.clear()
        self.uses.clear()
        self._groups = []
        self._run({"mode": "scan", "project": self.project_dir}, "Reading the pages", self._scanned)

    def _scanned(self, res: dict) -> None:
        self._groups = list(res.get("groups") or [])
        self.groups.clear()
        total = 0
        for g in self._groups:
            it = QtWidgets.QTreeWidgetItem(
                [
                    "",
                    "%d x %d%s" % (g["width"], g["height"], ", flashing" if g["flash"] else ""),
                    str(len(g["uses"])),
                    str(g["pages"]),
                    size_text(g["bytes"]),
                    size_text(g["wasted"]),
                ]
            )
            it.setIcon(0, _thumb_icon(g.get("thumb") or {}))
            it.setSizeHint(0, QtCore.QSize(56, 52))
            for c in (2, 3, 4, 5):
                it.setTextAlignment(c, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.groups.addTopLevelItem(it)
            total += g["wasted"]
        n = len(self._groups)
        text = (
            "No picture is stored more than once in the %d pages of %s." % (res.get("pages", 0), self.project)
            if not n
            else "%d picture%s stored more than once in %d pages: %s could be saved."
            % (n, " is" if n == 1 else "s are", res.get("pages", 0), size_text(total))
        )
        if res.get("failed"):
            text += " %d page%s could not be read." % (
                len(res["failed"]),
                "" if len(res["failed"]) == 1 else "s",
            )
        if res.get("stopped"):
            text = "Stopped. " + text
        self.status.setText(text)
        if n:
            self.groups.setCurrentItem(self.groups.topLevelItem(0))

    def _current(self) -> Optional[dict]:
        i = (
            self.groups.indexOfTopLevelItem(self.groups.currentItem())
            if self.groups.currentItem() is not None
            else -1
        )
        return self._groups[i] if 0 <= i < len(self._groups) else None

    def _show_uses(self) -> None:
        self.uses.clear()
        g = self._current()
        for u in (g or {}).get("uses") or ():
            l, t, r, b = u["rect"]
            self.uses.addTopLevelItem(
                QtWidgets.QTreeWidgetItem([u["name"], "%d, %d" % (l, t), "%d x %d" % (r - l, b - t)])
            )
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        g = self._current()
        idle = self._job is None
        for b in (self.b_symbol, self.b_replace):
            b.setEnabled(idle and g is not None)
        self.b_show.setEnabled(g is not None)
        self.b_scan.setEnabled(idle)

    def _show_copy(self) -> None:
        g = self._current()
        if g is None:
            return
        k = max(
            0,
            self.uses.indexOfTopLevelItem(self.uses.currentItem())
            if self.uses.currentItem() is not None
            else 0,
        )
        u = g["uses"][min(k, len(g["uses"]) - 1)]
        w = self._w
        try:
            w.open_document(u["page"])
        except Exception as e:  # noqa: BLE001 -- swallow-ok: said in the status line
            self.status.setText("%s could not be opened: %s" % (u["name"], e))
            return
        if w.doc is None:
            return
        i = locate_use(w.doc.parsed.page.objects, g["digest"], u)
        if i is None:
            # (the scan read the saved page; the open one has changed since)
            self.status.setText(
                "This copy is no longer where the scan found it on %s (the page changed "
                "since). Click Scan Again to update the list." % u["name"]
            )
            return
        w._set_selection([i])
        with contextlib.suppress(Exception):
            w.act_zoom_sel.trigger()

    # -- the changes -------------------------------------------------------------------
    def _confirm(self, g: dict, what: str) -> bool:
        pages = sorted({u["name"] for u in g["uses"]})
        ans = QtWidgets.QMessageBox.question(
            self,
            self.windowTitle().split(" - ")[0],
            "%s %d copies on %d page%s. The pages are backed up first (Tools > Restore "
            "Backup puts them back)." % (what, len(g["uses"]), len(pages), "" if len(pages) == 1 else "s"),
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Ok,
        )
        return ans == QtWidgets.QMessageBox.Ok

    def _base_request(self, g: dict, mode: str) -> dict:
        from ...infra import backups
        from ..jobs.helper_job import builder_open_pages

        in_builder = builder_open_pages()
        open_n = {os.path.normcase(os.path.abspath(p)) for p in self._open_paths()}
        return {
            "mode": mode,
            "digest": g["digest"],
            "pages": sorted(
                {u["page"] for u in g["uses"] if os.path.normcase(os.path.abspath(u["page"])) not in open_n}
            ),
            "skip": sorted(open_n),
            "builder_open": in_builder,
            "backup": backups.new_folder("pictures"),
        }

    def _make_symbol(self) -> None:
        g = self._current()
        if g is None or self._job is not None:
            return
        libs = sorted(
            os.path.splitext(os.path.basename(p))[0]
            for p in glob.glob(os.path.join(self.project_dir, "*.ctl"))
        )
        n = self.groups.indexOfTopLevelItem(self.groups.currentItem()) + 1
        dlg = _SymbolNameDialog(libs, "picture%d" % n, self.project_dir, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        lib, el = dlg.names()
        if dlg.exists():
            from ..messages import confirm

            if not confirm(
                self,
                "Make Library Symbol",
                "%s.%s already exists. Replace it with this picture?" % (lib, el),
                "Replace",
            ):
                return
        if not self._confirm(g, "The picture becomes the symbol %s.%s, linked from its" % (lib, el)):
            return
        req = self._base_request(g, "symbol")
        first = g["uses"][0]
        req.update(
            project=self.project_dir,
            library=lib,
            element=el,
            make=True,
            first={"page": first["page"], "index": first["index"]},
        )
        self._run(req, "Making the library symbol", lambda res: self._applied(res, g, "symbol", (lib, el)))

    def _replace_everywhere(self) -> None:
        g = self._current()
        if g is None or self._job is not None:
            return
        from .bitmap_import import PictureError, build, import_filter, open_source
        from .picture_import import PictureImportDialog

        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Replace Everywhere", self._w._dialog_dir("import"), import_filter()
        )
        if not path:
            return
        self._w._remember_dir("import", path)
        try:
            src = open_source(path)
        except PictureError as e:
            QtWidgets.QMessageBox.warning(self, "Replace Everywhere", str(e))
            return
        l, t, r, b = g["uses"][0]["rect"]
        dlg = PictureImportDialog(
            [src], self, target=(r - l, b - t), action="Replace", drawing=False, each_size=True
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        if not self._confirm(g, "%s replaces the picture in" % src.name):
            return
        opts = dlg.options_for(src)
        sizes = {(u["rect"][2] - u["rect"][0], u["rect"][3] - u["rect"][1]) for u in g["uses"]}
        req = self._base_request(g, "replace")
        from ...infra.paths import data_dir

        folder = data_dir("pictures")
        os.makedirs(folder, exist_ok=True)
        stem = os.path.join(folder, "%d-%s" % (os.getpid(), g["digest"][:12]))
        pixels = {}
        try:
            for w, h in sizes:
                pic = build(src, opts._replace(width=max(1, w), height=max(1, h)))
                f = "%s-%dx%d.rgba" % (stem, w, h)
                with open(f, "wb") as fh:
                    fh.write(pic.data)
                pixels["%dx%d" % (w, h)] = {"file": f, "w": pic.width, "h": pic.height}
        except (PictureError, OSError) as e:
            QtWidgets.QMessageBox.warning(self, "Replace Everywhere", str(e))
            return
        req["pixels"] = pixels
        self._job_files = [p["file"] for p in pixels.values()]
        self._run(req, "Replacing the picture", lambda res: self._applied(res, g, "replace", (src, opts)))

    def _applied(self, res: dict, g: dict, mode: str, what) -> None:
        """The worker's answer; then the pages open in an editor are changed there."""
        from ...format import bitmaps
        from ... import editing as editing_mod

        for f in getattr(self, "_job_files", ()):
            with contextlib.suppress(OSError):
                os.remove(f)
        self._job_files = []
        if mode == "symbol":
            # (the helper wrote the library: the open pages look for it again)
            editing_mod.libraries_changed()
        changed_open, failed_open = [], []
        for w, d in self._open_docs():
            objs = d.parsed.page.objects
            hits = [
                i
                for i, po in enumerate(objs)
                if po.type_code == bitmaps.BITMAP_TC
                and bitmaps.dib_section(po) is not None
                and d.free_picture(i)
                and bitmaps.picture_digest(po) == g["digest"]
            ]
            if not hits:
                continue
            name = os.path.splitext(os.path.basename(d.path))[0]
            try:
                with d.batch("make library symbol" if mode == "symbol" else "replace picture"):
                    for i in reversed(hits):
                        if mode == "symbol":
                            d.link_picture_to_symbol(i, *what)
                        else:
                            self._replace_in(d, i, *what)
                changed_open.append(name)
            except Exception as e:  # noqa: BLE001 -- swallow-ok: reported below
                failed_open.append("%s: %s" % (name, e))
            if w.doc is d:
                w._refresh()
        upd = list(res.get("updated") or [])
        failed = list(res.get("failed") or []) + failed_open
        text = "%s %d page%s" % (
            "Linked the copies to %s.%s on" % what if mode == "symbol" else "Replaced the picture on",
            len(upd) + len(changed_open),
            "" if len(upd) + len(changed_open) == 1 else "s",
        )
        if changed_open:
            text += " (%s open in the editor: save %s)" % (
                ", ".join(changed_open),
                "it" if len(changed_open) == 1 else "them",
            )
        text += "."
        if failed:
            text += " Could not change: %s." % "; ".join(failed)
        if res.get("open_in_builder"):
            text += " Left (open in the Graphics Builder): %s." % ", ".join(res["open_in_builder"])
        if res.get("stopped"):
            text = "Stopped. " + text
        self.status.setText(text)
        from ...infra import logsetup

        logsetup.get("pictures").info(
            "%s %s: updated %s, open %s, failed %s, backup %s",
            mode,
            g["digest"],
            upd,
            changed_open,
            failed,
            res.get("backup"),
        )
        self.scan_after = True
        QtCore.QTimer.singleShot(0, self, self.scan)

    @staticmethod
    def _replace_in(doc, i: int, src, opts) -> None:
        from ...format import geometry as G
        from .bitmap_import import build

        l, t, r, b = G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1)
        pic = build(src, opts._replace(width=max(1, r - l), height=max(1, b - t)))
        doc.replace_bitmap(i, (l, t, r, b), pic.data, pic.width, pic.height, flash_rgba=None)
