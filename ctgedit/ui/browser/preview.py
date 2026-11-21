"""The preview pane: the embedded thumbnail first, a real render on request."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import contextlib
import datetime
import hashlib
import json
import os
import sys
import time
import weakref
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...io.catalog import Entry, ProjectCatalog
from ..panels import panel_options as _panels
from ..style.theme import COLORS, type_qss
from .thumbs import PREVIEW_SIZE, ThumbnailService, load_summary


# ---------------------------------------------------------------------------
# Offscreen page render + the render process
# ---------------------------------------------------------------------------


def render_scale(page, max_w: int, max_h: int) -> float:
    """Image pixels per page pixel of :func:`render_page_image` for ``max_w`` x ``max_h``."""
    w = int(page.appearance.width or 1024)
    h = int(page.appearance.height or 768)
    scale = min(max_w / float(w), max_h / float(h)) if w and h else 1.0
    return max(0.02, min(scale, 4.0))


def is_template_root(page, i: int) -> bool:
    """Object ``i`` of ``page`` is the page template's root (its members are the template's
    objects)."""
    o = page.objects[i]
    return bool(
        o.parent is None
        and o.members
        and (o.raw.get("_from_template") or str(o.raw.get("_ref") or "").lower().endswith("ctt"))
    )


def render_page_image(
    page, max_w: int, max_h: int, transparent: bool = False, template: bool = True
) -> QtGui.QImage:
    """Render a parsed Page to a QImage. GUI thread only."""
    from ...render.scene import PageScene

    w = int(page.appearance.width or 1024)
    h = int(page.appearance.height or 768)
    scale = render_scale(page, max_w, max_h)
    tw, th = max(1, int(w * scale)), max(1, int(h * scale))
    scene = PageScene()
    scene.set_pane_labels_visible(False)  # a picture of the page: no editor badges
    try:
        scene.set_page(page, thumbnail_opacity=0.0)
        img = QtGui.QImage(tw, th, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtCore.Qt.transparent if transparent else QtGui.QColor("#d4d0c8"))
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        try:
            if transparent:
                # (every object, nothing of the page's background or the editor's marks)
                keep = set(range(len(page.objects)))
                if not template:
                    for i in page.roots():
                        if is_template_root(page, i):
                            keep.difference_update(page.subtree(i))
                with scene.export_only(sorted(keep)):
                    scene.render(p, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(0, 0, w, h))
            else:
                scene.render(p, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(0, 0, w, h))
        finally:
            p.end()
    finally:
        scene.clear()
        scene.deleteLater()
    return img


def image_png(img: QtGui.QImage, quality: int = -1) -> bytes:
    """``img`` as PNG bytes (``quality`` 0 to 100: Qt's PNG compression, -1 its default;
    a higher number packs less and is faster)."""
    ba = QtCore.QByteArray()
    buf = QtCore.QBuffer(ba)
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG", quality)
    buf.close()
    return bytes(ba.data())


#: at most this many object boxes in a render's result (render_to_files' 'boxes' option)
MAX_BOXES = 5000


def root_boxes(page) -> List[list]:
    """[x, y, w, h] of the page's top-level objects (their Extents, the boxes the canvas's
    smart guides take), without the page template's root (its box is the page) and without
    the objects wholly off the page: the picture shows the page only, and a snap to an
    object nobody sees would catch on an empty spot."""
    out: List[list] = []
    pw = float(page.appearance.width or 1024)
    ph = float(page.appearance.height or 768)
    for i in page.roots():
        if is_template_root(page, i):
            continue
        g = page.objects[i].geometry
        if g is None or (g.extent_x <= 0 and g.extent_y <= 0):
            continue
        x0, y0 = float(g.x), float(g.y)
        x1, y1 = x0 + max(0, g.extent_x), y0 + max(0, g.extent_y)
        if x1 < 0 or y1 < 0 or x0 > pw or y0 > ph:
            continue
        out.append([float(g.x), float(g.y), float(max(0, g.extent_x)), float(max(0, g.extent_y))])
        if len(out) >= MAX_BOXES:
            break
    return out


def _page_from_bytes(path: str, as_path: str):
    """The page in file ``path`` (an earlier version: infra/history) drawn as the page at
    ``as_path`` (its project's libraries), as File > Page History's Open Read-only shows it."""
    from ...editing import EditableDocument

    with open(path, "rb") as fh:
        data = fh.read()
    return EditableDocument.from_bytes(data, as_path).page


def preview_command(out_base: str) -> List[str]:
    """The render process's command line (it waits for its request in
    ``<out_base>.req``, :func:`preview_main`): ``ctgedit.exe --preview-render``
    in the frozen build, else this Python running :func:`preview_main`."""
    from ...infra.paths import process_started

    args = ["--serve", out_base, str(os.getpid()), str(process_started())]
    from ...infra import workers

    return workers.command("--preview-render", "ctgedit.ui.browser.preview", "preview_main", args)


def _quiet_crashes() -> None:
    """No Windows error box for a crash of the render process (the editor
    reports the missing result itself)."""
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import ctypes

            # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)


#: seconds a render process waits for its request before it ends by itself
SERVE_IDLE_S = 300.0


def _opened_page(path: str):
    """The page as the editor shows it once open: with the open-time updates (changed
    symbols, genies and template applied again, EditableDocument.apply_open_updates, as the
    Settings ask), as CtDraw's load does; the reader's page when it cannot be opened for
    editing."""
    from ...io import reader

    try:
        from ...editing import EditableDocument
        from ..prefs.settings import app_settings

        s = app_settings()
        genies = s.value("opt/auto_update_genies", True, type=bool)
        pre = []

        def remembered(d) -> bool:
            # the genie update the editor's worker composed for these bytes before: applied
            # at once (each preview is a fresh process: else a stale page's genies are
            # composed from scratch on every preview)
            from ..jobs import genie_jobs

            pre.append(genie_jobs.preapply_answer(d) if genies and not d.read_only else None)
            return pre[-1] is not None

        doc = EditableDocument.open(path, before_derive=remembered)
        doc.apply_open_updates(
            symbols=s.value("opt/auto_update_symbols", True, type=bool),
            genies=genies and not any(pre),
            template=s.value("opt/auto_update_template", True, type=bool),
            placeholders=s.value("opt/auto_placeholder_missing", True, type=bool),
        )
        if doc.page is not None:
            return doc.page
    except Exception:  # noqa: BLE001 -- the reader's page then
        from ...infra import logsetup

        logsetup.note_exception("preview._opened_page", page=path)
    return reader.read_page(path)


def render_to_files(
    page: str, out: str, max_w: int, max_h: int, mode: str, options: Optional[dict] = None
) -> dict:
    """Parse and render ``page`` to ``<out>.png`` and ``<out>.json``."""
    from ...format.fileio import write_atomic

    opts = dict(options or {})
    res: dict = {}
    try:
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["ctgedit-preview"])
        from ...io import reader

        reader.set_label_mode(mode)
        as_path = str(opts.get("as_path") or "")
        pg = _page_from_bytes(page, as_path) if as_path else _opened_page(page)
        mw, mh = max(1, int(max_w)), max(1, int(max_h))
        factor = float(opts.get("factor") or 0)
        if factor > 0:
            # (at most ``factor`` picture pixels a page pixel, whatever room is offered)
            mw = min(mw, max(1, int((pg.appearance.width or 1024) * factor)))
            mh = min(mh, max(1, int((pg.appearance.height or 768) * factor)))
        max_px = float(opts.get("max_pixels") or 0)
        if max_px > 0:
            pw0 = float(pg.appearance.width or 1024)
            ph0 = float(pg.appearance.height or 768)
            s = render_scale(pg, mw, mh)
            if pw0 * ph0 * s * s > max_px:
                k = (max_px / (pw0 * ph0)) ** 0.5
                mw, mh = min(mw, max(1, int(pw0 * k))), min(mh, max(1, int(ph0 * k)))
        img = render_page_image(
            pg, mw, mh, transparent=bool(opts.get("transparent")), template=bool(opts.get("template", True))
        )
        png = image_png(img, 80 if opts.get("fast") else -1)
        write_atomic(out + ".png", png)
        res = {
            "objects": len(pg.objects),
            "sha1": hashlib.sha1(png).hexdigest(),
            "size": [img.width(), img.height()],
            "page": [int(pg.appearance.width or 1024), int(pg.appearance.height or 768)],
            "scale": render_scale(pg, mw, mh),
        }
        if opts.get("boxes"):
            res["boxes"] = root_boxes(pg)
    except Exception as e:  # noqa: BLE001 -- reported to the editor
        res = {"error": str(e).strip() or "the page could not be read"}
    with contextlib.suppress(OSError):
        write_atomic(out + ".json", json.dumps(res).encode("utf-8"))
    return res


def preview_main(argv=None) -> int:
    """The render process (``ctgedit --preview-render``)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--preview-render"]:
        args = args[1:]
    _quiet_crashes()
    if len(args) == 5 and args[0] != "--serve":
        page, out, w, h, mode = args
        return 0 if "error" not in render_to_files(page, out, int(w), int(h), mode) else 1
    if len(args) != 4 or args[0] != "--serve":
        return 2
    out, pid, started = args[1], int(args[2]), int(args[3])
    from ...infra.paths import pid_alive

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["ctgedit-preview"])
    from ...io import reader  # noqa: F401 -- warm: the imports a render needs
    from ...render import scene  # noqa: F401

    t0 = last = time.monotonic()
    req_path = out + ".req"
    while True:
        if os.path.exists(req_path):
            try:
                with open(req_path, "rb") as fh:
                    req = json.loads(fh.read().decode("utf-8"))
            except OSError, ValueError:
                time.sleep(0.005)  # (being written: write_atomic renames it in)
                continue
            with contextlib.suppress(OSError):
                os.remove(req_path)
            res = render_to_files(
                req["page"],
                out,
                int(req["w"]),
                int(req["h"]),
                req.get("mode") or "native",
                req.get("options") or None,
            )
            return 0 if "error" not in res else 1
        now = time.monotonic()
        if now - t0 > SERVE_IDLE_S:
            return 0
        if now - last > 0.5:
            last = now
            if not pid_alive(pid, started):
                return 0
        time.sleep(0.01)


class _PreviewRenderer(QtCore.QObject):
    """Runs render processes one at a time; the newest request wins."""

    #: seconds before a render process counts as hung (killed, reported): Settings > Pages
    #: and libraries > Long jobs (ui/prefs/job_options)
    TIMEOUT_S = 120.0
    POLL_MS = 25

    def __init__(self, parent=None):
        super().__init__(parent)
        self._job: Optional[dict] = None
        self._spare: Optional[dict] = None
        self._panes = 0
        self._warm = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._swept = False

    # -- panes (a spare process only while one exists) --------------------------
    def attach(self, pane: QtCore.QObject) -> None:
        self._panes += 1
        pane.destroyed.connect(self._detach)

    def _detach(self, *_a) -> None:
        self._panes = max(0, self._panes - 1)
        if not self._panes:
            self._kill(self._spare)
            self._spare = None

    def busy(self) -> bool:
        return self._job is not None

    # -- processes -------------------------------------------------------------------
    def _spawn(self) -> dict:
        """A new render process waiting for its request (raises when it cannot start)."""
        import tempfile
        from ...infra.paths import data_dir

        folder = data_dir("preview")
        os.makedirs(folder, exist_ok=True)
        self._sweep(folder)
        fd, base = tempfile.mkstemp(prefix="%d-" % os.getpid(), dir=folder)
        os.close(fd)
        os.remove(base)
        from ...infra import workers

        # (normal priority: the user waits for the preview)
        proc = workers.start(preview_command(base), low_priority=False)
        return {"proc": proc, "out": base}

    def prestart(self) -> None:
        """Have a render process waiting for the next request."""
        sp = self._spare
        if sp is not None and sp["proc"].poll() is None:
            return
        self._discard(sp)
        self._spare = None
        with contextlib.suppress(Exception):
            self._spare = self._spawn()

    def request(
        self, token: int, path: str, max_w: int, max_h: int, target, options: Optional[dict] = None
    ) -> None:
        """Render ``path`` for ``target`` (weakly referenced) at ``max_w`` x ``max_h``
        (``options``: :func:`render_to_files`')."""
        self.cancel()
        from ...format.fileio import write_atomic
        from ...io import reader

        job = {
            "token": token,
            "target": weakref.ref(target),
            "t0": time.monotonic(),
            "proc": None,
            "out": None,
        }
        err = ""
        try:
            sp, self._spare = self._spare, None
            if sp is None or sp["proc"].poll() is not None:
                self._discard(sp)
                sp = self._spawn()
            job.update(sp)
            req = {"page": path, "w": int(max_w), "h": int(max_h), "mode": reader.get_label_mode()}
            if options:
                req["options"] = dict(options)
            write_atomic(job["out"] + ".req", json.dumps(req).encode("utf-8"))
        except Exception as e:  # noqa: BLE001 -- reported to the pane
            err = "could not start the render process: %s: %s" % (type(e).__name__, e)
        if err:
            self._kill(job)
            self._deliver(job, None, {}, err)
            return
        self._warm = True
        self._job = job
        self._timer.start()

    def cancel_for(self, target) -> None:
        """Stop the running render when it is ``target``'s (its selection moved on)."""
        job = self._job
        if job is not None and job["target"]() is target:
            self.cancel()

    def cancel(self) -> None:
        """Stop the running render (its result is not delivered)."""
        job, self._job = self._job, None
        self._timer.stop()
        self._kill(job)

    def shutdown(self) -> None:
        """Stop every render process (the application quits)."""
        self.cancel()
        self._kill(self._spare)
        self._spare = None

    def wait(self, timeout: float = 60.0) -> bool:
        """Block until the running render is delivered (tests / scripts)."""
        t0 = time.monotonic()
        while self._job is not None and time.monotonic() - t0 < timeout:
            proc = self._job.get("proc")
            if proc is not None:
                with contextlib.suppress(Exception):
                    proc.wait(0.05)
            self._poll()
        return self._job is None

    def _poll(self) -> None:
        job = self._job
        if job is None:
            self._timer.stop()
            return
        proc = job["proc"]
        code = proc.poll()
        if code is None:
            if job["target"]() is None:
                self.cancel()  # nobody wants it any more (the pane is gone)
            elif time.monotonic() - job["t0"] > self.TIMEOUT_S:
                self.cancel()
                self._deliver(job, None, {}, "the render process did not finish in %d s" % self.TIMEOUT_S)
            return
        self._job = None
        self._timer.stop()
        img, info, err = None, {}, ""
        try:
            with open(job["out"] + ".json", "rb") as fh:
                info = json.loads(fh.read().decode("utf-8"))
        except OSError, ValueError:
            info = {}
            err = "the render process ended (exit code %s) without a result" % code
        if not err and "error" in info:
            err = str(info["error"])
        if not err:
            img = QtGui.QImage()
            if not img.load(job["out"] + ".png", "PNG") or img.isNull():
                img, err = None, "the render process wrote no picture"
        self._discard(job)
        if self._warm and self._panes:
            self.prestart()
        self._deliver(job, img, info, err)

    @staticmethod
    def _deliver(job: dict, img, info: dict, err: str) -> None:
        target = job["target"]()
        if target is None:
            return
        try:
            target._on_rendered(job["token"], img, info, err)
        except RuntimeError:  # the pane's C++ side is gone (dialog closed)
            pass

    def _kill(self, job: Optional[dict]) -> None:
        if job is None:
            return
        proc = job.get("proc")
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()
                proc.wait(5)
        self._discard(job)

    @staticmethod
    def _discard(job: Optional[dict]) -> None:
        base = job.get("out") if job else None
        if base:
            for ext in (".png", ".json", ".req"):
                with contextlib.suppress(OSError):
                    os.remove(base + ext)

    def _sweep(self, folder: str) -> None:
        """Once per run: remove results older than a day (a render the editor
        was closed during)."""
        if self._swept:
            return
        self._swept = True
        old = time.time() - 86400
        with contextlib.suppress(OSError):
            for name in os.listdir(folder):
                p = os.path.join(folder, name)
                with contextlib.suppress(OSError):
                    if os.path.getmtime(p) < old:
                        os.remove(p)


#: the application's render-process runners by name ('preview': the panes; 'underlay': the
#: canvas's underlays, ui/editor/underlay.py: one runner each, so neither stops the other's render)
_RENDERERS: Dict[str, _PreviewRenderer] = {}
_NEXT_TOKEN = [0]


def _new_token() -> int:
    _NEXT_TOKEN[0] += 1
    return _NEXT_TOKEN[0]


def set_timeout(seconds: float) -> None:
    """Settings > Pages and libraries > Long jobs > "Give up a live preview after"
    (ui/prefs/job_options): a preview that takes longer is stopped."""
    _PreviewRenderer.TIMEOUT_S = float(seconds)


def renderer(name: str = "preview") -> _PreviewRenderer:
    """The application's render-process runner ``name``."""
    r = _RENDERERS.get(name)
    if r is not None:
        try:
            r.objectName()  # still alive?
            return r
        except RuntimeError:
            _RENDERERS.pop(name, None)
    app = QtWidgets.QApplication.instance()
    r = _RENDERERS[name] = _PreviewRenderer(app)
    if app is not None:
        app.aboutToQuit.connect(r.shutdown)
    return r


# ---------------------------------------------------------------------------
# The pane
# ---------------------------------------------------------------------------


class _ImageLabel(QtWidgets.QLabel):
    """A label that keeps a source pixmap and rescales it on resize."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src: Optional[QtGui.QPixmap] = None
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(PREVIEW_SIZE)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.setStyleSheet(
            "background:%s; border:1px solid %s; color:%s;"
            % (COLORS["deep"], COLORS["border"], COLORS["text_dim"])
        )

    def set_source(self, pm: Optional[QtGui.QPixmap], text: str = "") -> None:
        self._src = pm if pm is not None and not pm.isNull() else None
        if self._src is None:
            self.setPixmap(QtGui.QPixmap())
            self.setText(text)
        else:
            self.setText("")
            self._rescale()

    def source(self) -> Optional[QtGui.QPixmap]:
        return self._src

    def _rescale(self) -> None:
        if self._src is None:
            return
        avail = self.contentsRect().size() - QtCore.QSize(6, 6)
        if avail.width() < 8 or avail.height() < 8:
            return
        self.setPixmap(self._src.scaled(avail, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))

    def resizeEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(ev)
        self._rescale()

    def heightForWidth(self, w: int) -> int:  # noqa: N802 (Qt API)
        return max(PREVIEW_SIZE.height(), int(w * 0.62))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True


class PreviewPane(QtWidgets.QFrame):
    """Right-hand preview + metadata for the selected browser entry."""

    openPathRequested = QtCore.Signal(str)  # 'Open Active Copy'
    selectPageRequested = QtCore.Signal(str)  # 'Show <renamed page>'

    PAGE_FIELDS = (
        "Title",
        "Comment",
        "Area",
        "Content type",
        "Project",
        "Template",
        "Resolution",
        "Size",
        "Objects",
        "Modified",
        "Registered",
    )
    ELEMENT_FIELDS = (
        "Reference",
        "Library",
        "Project",
        "Resolution",
        "Size",
        "Objects",
        "Modified",
        "Search order",
    )

    #: Settings 'The preview shows: A fresh drawing of the page': the pause (ms) on a page
    #: before it is drawn
    LIVE_WAIT_MS = 250

    def __init__(self, thumbs: ThumbnailService, catalog: ProjectCatalog, parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.catalog = catalog
        self._entry: Optional[Entry] = None
        self._summary = None
        self._token = 0
        self._live_for: Optional[str] = None  # entry key the live image belongs to
        self.live_image: Optional[QtGui.QImage] = None
        self.auto_live = True  # live-render pages that have no thumbnail
        # Settings 'A fresh drawing of the page': the drawing starts after a short pause on
        # the page (arrowing through the grid starts no render process per page passed) and
        # only while the pane shows (the key of the entry it waits to draw)
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(self.LIVE_WAIT_MS)
        self._live_timer.timeout.connect(self._live_due)
        self._live_wait: Optional[str] = None
        self.setObjectName("browserPreview")
        self.setMinimumWidth(320)
        self.setStyleSheet(
            "QFrame#browserPreview { background:%s; border-left:1px solid %s; }"
            % (COLORS["panel"], COLORS["border"])
        )

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(3), _sp(3), _sp(3), _sp(2))
        lay.setSpacing(_sp(1))
        self.image = _ImageLabel(self)
        lay.addWidget(self.image)

        live_row = QtWidgets.QHBoxLayout()
        self.btn_live = QtWidgets.QPushButton("Live Render")
        self.btn_live.setToolTip("Draw the page as the editor shows it (Space)")
        self.btn_live.clicked.connect(self.live_render)
        self.btn_live.setAutoDefault(False)
        self.live_status = QtWidgets.QLabel("")
        self.live_status.setStyleSheet("color:%s;" % COLORS["text_dim"])
        live_row.addWidget(self.btn_live)
        live_row.addWidget(self.live_status, 1)
        lay.addLayout(live_row)

        self.name = QtWidgets.QLabel()
        self.name.setWordWrap(True)
        self.name.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.name.setStyleSheet("%s color:%s;" % (type_qss("subtitle"), COLORS["head"]))
        lay.addWidget(self.name)
        self.subtitle = QtWidgets.QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("color:%s;" % COLORS["text_dim"])
        lay.addWidget(self.subtitle)

        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("browserBanner")
        self.banner.setStyleSheet(
            "QFrame#browserBanner { background:%s; border:1px solid %s;"
            " border-radius:4px; }" % (COLORS["banner_bg"], COLORS["banner_edge"])
        )
        bl = QtWidgets.QVBoxLayout(self.banner)
        bl.setContentsMargins(_sp(2), _sp(1), _sp(2), _sp(1))
        self.banner_text = QtWidgets.QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_text.setStyleSheet(
            "border:none; background:transparent; color:%s;" % COLORS["banner_text"]
        )
        bl.addWidget(self.banner_text)
        brow = QtWidgets.QHBoxLayout()
        self.btn_banner = QtWidgets.QPushButton()
        self.btn_banner.hide()
        self.btn_banner.setAutoDefault(False)
        self.btn_banner.clicked.connect(self._banner_action)
        brow.addWidget(self.btn_banner)
        brow.addStretch(1)
        bl.addLayout(brow)
        self.banner.hide()
        lay.addWidget(self.banner)
        self._banner_target: tuple = ()

        self.form = QtWidgets.QFormLayout()
        self.form.setLabelAlignment(QtCore.Qt.AlignRight)
        self.form.setHorizontalSpacing(_sp(2))
        self.form.setVerticalSpacing(_sp(0))
        self.fields: Dict[str, QtWidgets.QLabel] = {}
        self._labels: Dict[str, QtWidgets.QLabel] = {}
        # one form for both: an element's reference and library come first
        order = ("Reference", "Library") + self.PAGE_FIELDS + self.ELEMENT_FIELDS
        for k in dict.fromkeys(order):
            v = QtWidgets.QLabel()
            v.setWordWrap(True)
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            lab = QtWidgets.QLabel(k + ":")
            lab.setStyleSheet("color:%s;" % COLORS["text_dim"])
            self.form.addRow(lab, v)
            self.fields[k] = v
            self._labels[k] = lab
        lay.addLayout(self.form)
        lay.addStretch(1)

        prow = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLabel()
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.path.setStyleSheet("color:%s; %s" % (COLORS["text_dim"], type_qss("caption")))
        self.btn_copy = QtWidgets.QToolButton()
        self.btn_copy.setText("Copy")
        self.btn_copy.setToolTip("Copy the path (Ctrl+Shift+C)")
        self.btn_copy.clicked.connect(self.copy_path)
        prow.addWidget(self.path, 1)
        prow.addWidget(self.btn_copy, 0, QtCore.Qt.AlignTop)
        lay.addLayout(prow)

        thumbs.thumbnailReady.connect(self._on_thumb)
        renderer().attach(self)
        self.show_entry(None)

    # -- helpers ---------------------------------------------------------------
    def _show_fields(self, names) -> None:
        want = set(names)
        for k, v in self.fields.items():
            vis = k in want
            v.setVisible(vis)
            self._labels[k].setVisible(vis)

    def _set(self, key: str, text: str) -> None:
        """One details row; an empty one is hidden (no label over nothing)."""
        self.fields[key].setText(text or "")
        if not text:
            self.fields[key].setVisible(False)
            self._labels[key].setVisible(False)

    def current_entry(self) -> Optional[Entry]:
        return self._entry

    def copy_path(self) -> None:
        if self._entry is not None:
            QtWidgets.QApplication.clipboard().setText(self._entry.path)

    # -- entry display ------------------------------------------------------------
    def show_entry(self, e: Optional[Entry]) -> None:
        self._entry = e
        self._live_timer.stop()  # (a fresh drawing waits for the new entry)
        self._live_wait = None
        self._token = _new_token()  # a pending live render is now stale
        renderer().cancel_for(self)  # ... and its process not needed
        self.live_image = None
        self._live_for = None
        self.live_status.setText("")
        self.banner.hide()
        self.btn_banner.hide()
        if e is None:
            self.image.set_source(None, "Nothing selected")
            self.name.setText("")
            self.subtitle.setText("")
            self.path.setText("")
            self._show_fields(())
            self.btn_live.setEnabled(False)
            self.btn_copy.setEnabled(False)
            return
        self.btn_copy.setEnabled(True)
        self.btn_live.setEnabled(e.kind == "page")
        self.btn_live.setToolTip(
            "Draw the page as the editor shows it (Space)"
            if e.kind == "page"
            else "Live Render draws pages only"
        )
        self._summary = load_summary(e, want_thumbnail=False)
        self._refresh_image()
        self.name.setText(e.name)
        self.path.setText(e.path)
        if e.kind == "page":
            self._fill_page(e)
        else:
            self._fill_element(e)
        if self._can_draw(e):
            if self.auto_live and self._no_thumbnail(e):
                self.live_render()
            elif self.always_live():
                self._wait_to_draw()

    def _can_draw(self, e: Optional[Entry]) -> bool:
        """``e`` is a page whose summary was read (a live render can draw it)."""
        return (
            e is not None
            and e.kind == "page"
            and self._summary is not None
            and bool(getattr(self._summary, "ok", False))
        )

    def _wait_to_draw(self) -> None:
        """A fresh drawing of the page shown, after :attr:`LIVE_WAIT_MS` on it (another
        selection starts the wait again)."""
        e = self._entry
        if e is None or self._live_for == e.key:
            return  # (drawn or being drawn)
        self._live_wait = e.key
        self._live_timer.start()

    def _live_due(self) -> None:
        e = self._entry
        if e is None or e.key != self._live_wait or not self.isVisible() or self._live_for == e.key:
            return  # (another page, the pane hidden, or drawn)
        self._live_wait = None
        self.live_render()

    def showEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().showEvent(ev)
        # (the pane shown again, the window made wide: the page selected is drawn now)
        if self.always_live() and self._can_draw(self._entry) and self.live_image is None:
            self._wait_to_draw()

    def hideEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().hideEvent(ev)
        self._live_timer.stop()

    @staticmethod
    def always_live() -> bool:
        """Settings > Pages and libraries > Open and Save As > The preview shows: A fresh
        drawing of the page (each page selected is drawn while the pane shows; the saved
        picture shows until the drawing comes)."""
        return _panels.PREVIEW_LIVE == "live"

    def _no_thumbnail(self, e: Entry) -> bool:
        return self.thumbs.has_no_thumbnail(e)

    def _refresh_image(self) -> None:
        e = self._entry
        if e is None:
            return
        if self.live_image is not None and self._live_for == e.key:
            self.image.set_source(QtGui.QPixmap.fromImage(self.live_image))
            return
        pm = self.thumbs.request_full(e)
        if pm is not None:
            self.image.set_source(pm)
        elif self.thumbs.has_no_thumbnail(e):
            self.image.set_source(
                None, "No embedded preview" + (" (use Live Render)" if e.kind == "page" else "")
            )
        else:
            self.image.set_source(None, "Loading…")

    def _fill_page(self, e: Entry) -> None:
        s = self._summary
        self._show_fields(self.PAGE_FIELDS)
        self.subtitle.setText(e.title if e.title and e.title != e.name else "")
        # (the title only when it says more than the name above it)
        self._set("Title", e.title if e.title and e.title.strip().lower() != e.name.lower() else "")
        self._set("Comment", e.comment)
        self._set("Area", e.area or getattr(s, "area_code", ""))
        self._set("Content type", getattr(e, "content_type", ""))
        self._set("Project", e.project + ("" if not e.is_other_install else " (other install)"))
        t = getattr(s, "template", None)
        if t is not None:
            self._set("Template", "%s (%s)" % (t.name, t.library))
            self._set("Resolution", self._resolution_text(t.resolution, t.title_bar))
        else:
            self._set("Template", "")
            self._set("Resolution", "")
        w, h = getattr(s, "width", 0), getattr(s, "height", 0)
        self._set("Size", "%d × %d px" % (w, h) if w and h else "")
        self._set("Objects", str(s.object_count) if s is not None and s.ok else "")
        self._set("Modified", self._modified(e))
        if e.registered is None:
            self._set("Registered", "")
        elif e.registered:
            self._set("Registered", "Yes")
        else:
            self.fields["Registered"].setText(
                "<span style='color:%s'>No (not in pages.DBF)</span>" % COLORS["warn"]
            )
        self._banners_page(e)

    @staticmethod
    def _resolution_text(res: str, title_bar: bool) -> str:
        """``'HD1080, title bar'`` / ``'HD1080, no title bar'``."""
        bar = "title bar" if title_bar else "no title bar"
        return "%s, %s" % (res, bar) if res else bar.capitalize()

    def _fill_element(self, e: Entry) -> None:
        s = self._summary
        self._show_fields(
            self.ELEMENT_FIELDS
            if e.kind == "template"
            else tuple(f for f in self.ELEMENT_FIELDS if f != "Resolution")
        )
        self.subtitle.setText(
            "%s element in %s"
            % ({"supergenie": "Super genie"}.get(e.kind, e.kind.capitalize()), os.path.basename(e.path))
        )
        self._set("Reference", e.reference)
        self._set("Library", os.path.basename(e.path))
        self._set("Project", e.project + ("" if not e.is_other_install else " (other install)"))
        if e.kind == "template":
            self._set("Resolution", self._resolution_text(e.resolution, e.title_bar))
        w, h = getattr(s, "width", 0), getattr(s, "height", 0)
        self._set("Size", "%d × %d px" % (w, h) if w and h else "")
        self._set("Objects", str(s.object_count) if s is not None and s.ok else "")
        self._set("Modified", self._modified(e))
        self._set(
            "Search order",
            ("Hidden by the copy in %s" % e.shadowed_by) if e.shadowed_by else "First on the include chain",
        )
        if e.shadowed_by:
            self._banner("Hidden by the copy in %s, which the Graphics Builder uses." % e.shadowed_by)
        elif e.is_other_install:
            self._banner_other_install(e)

    @staticmethod
    def _modified(e: Entry) -> str:
        if not e.mtime_ns:
            return ""
        return datetime.datetime.fromtimestamp(e.mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M")

    # -- banners -----------------------------------------------------------------
    def _banner(self, text: str, button: str = "", target: tuple = ()) -> None:
        self.banner_text.setText(text)
        self._banner_target = target
        if button:
            self.btn_banner.setText(button)
            self.btn_banner.show()
        else:
            self.btn_banner.hide()
        self.banner.show()

    def _banner_other_install(self, e: Entry) -> None:
        live = self.catalog.live_install
        where = live.user_dir if live is not None else "another install"
        copy = self.catalog.live_copy_of(e.path) if e.kind == "page" else None
        self._banner(
            "Other install. The Graphics Builder uses %s and does not see edits to this copy." % where,
            "Open Active Copy" if copy else "",
            ("open", copy) if copy else (),
        )

    def _banners_page(self, e: Entry) -> None:
        if e.is_other_install:
            self._banner_other_install(e)
            return
        if e.registered is False:
            if e.superseded_by:
                self._banner(
                    "Not registered in pages.DBF, so it is not compiled. It looks "
                    "renamed to %s (registered, newer)." % e.superseded_by,
                    "Show %s" % e.superseded_by,
                    ("select", e.superseded_by),
                )
            else:
                self._banner("Not registered in pages.DBF, so it is not compiled.")

    def _banner_action(self) -> None:
        if not self._banner_target:
            return
        what, arg = self._banner_target
        if what == "open" and arg:
            self.openPathRequested.emit(arg)
        elif what == "select" and arg:
            self.selectPageRequested.emit(arg)

    # -- thumbnails / live render ----------------------------------------------------
    def _on_thumb(self, key: str) -> None:
        try:
            if self._entry is not None and key == self._entry.key:
                self._refresh_image()
                if (
                    self.auto_live
                    and self._entry.kind == "page"
                    and self.live_image is None
                    and self.thumbs.has_no_thumbnail(self._entry)
                    and self._live_for is None
                ):
                    self.live_render()
        except RuntimeError:
            pass

    def live_render(self) -> None:
        """Parse + render the selected page in the render process (the result
        comes back to :meth:`_on_rendered`)."""
        e = self._entry
        if e is None or e.kind != "page":
            return
        token = self._token = _new_token()
        self._live_for = e.key
        self.live_status.setText("Rendering…")
        max_w, max_h = self._render_size()
        renderer().request(token, e.path, max_w, max_h, self)

    def _render_size(self):
        size = self.image.contentsRect().size()
        dpr = self.devicePixelRatioF() or 1.0
        return (max(320, int(size.width() * dpr * 2)), max(200, int(size.height() * dpr * 2)))

    def _on_rendered(self, token: int, img, info: dict, err: str) -> None:
        if token != self._token or self._entry is None:
            return  # selection moved on
        if img is None:
            self.live_status.setText("Render failed: %s" % err)
            self._live_for = None
            return
        self.live_image = img
        self.live_status.setText("Live render · %d objects" % int(info.get("objects", 0)))
        self.image.set_source(QtGui.QPixmap.fromImage(img))

    def render_now(self) -> Optional[QtGui.QImage]:
        """Synchronous live render of the selected page (tests / scripts)."""
        e = self._entry
        if e is None or e.kind != "page":
            return None
        from ...io.reader import read_page

        tok = self._token = _new_token()
        self._live_for = e.key
        try:
            page = read_page(e.path)
            err = ""
        except Exception as ex:  # noqa: BLE001
            page, err = None, str(ex).strip() or "the page could not be read"
        self._on_parsed(tok, page, err)
        return self.live_image

    def _on_parsed(self, token: int, page, err: str) -> None:
        """A page parsed HERE (:meth:`render_now`): render it on this thread."""
        if token != self._token or self._entry is None:
            return  # selection moved on
        if page is None:
            self.live_status.setText("Render failed: %s" % err)
            self._live_for = None
            return
        max_w, max_h = self._render_size()
        try:
            img = render_page_image(page, max_w, max_h)
        except Exception as ex:  # noqa: BLE001 -- a renderer bug must not kill the dialog
            self.live_status.setText("Render failed: %s" % ex)
            self._live_for = None
            return
        self._on_rendered(token, img, {"objects": len(page.objects)}, "")
