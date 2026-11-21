"""Drive a long editor session and compare it with fresh-process baselines.

python tools/session_soak.py [--group example|examplesa|<g1,g2>] [--pages ...]
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if os.environ.get("CTG_HERMETIC") == "1":
    # run by a hermetic test (tests/test_session_soak.py): the test's guard holds here too
    # (its import sets a test's starting environment: the child keeps the test's own, its
    # fake install)
    _env = dict(os.environ)
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import hermetic  # noqa: E402

    sys.path.pop(0)
    os.environ.clear()
    os.environ.update(_env)
    hermetic.guard_child()

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
GROUPS = {
    "example": ["Example\\Mixer.ctg", "Example\\Genies.ctg", "Example\\Groups.ctg", "Example\\!PID.ctg"],
    "examplesa": ["ExampleSA\\Mixing_L3.ctg", "ExampleSA\\Overview_L2.ctg"],
}


def _user_groups() -> dict:
    """The groups of the user's own pages: the role session_soak_groups of
    tests_corpus/local.json ({group: ['Project/Page.ctg', ...]}), in the tool's
    'Project\\Page.ctg' form. Empty under the hermetic guard (no pages there)."""
    if os.environ.get("CTG_HERMETIC") == "1":
        return {}
    sys.path.insert(0, os.path.join(ROOT, "tests_corpus"))
    try:
        import corpus  # noqa: E402
    except ImportError:
        return {}
    finally:
        sys.path.pop(0)
    groups = corpus.role_value("session_soak_groups", {})
    if not isinstance(groups, dict):
        return {}
    return {
        str(name).lower(): [p.strip().strip("\\/").replace("/", "\\") for p in pages]
        for name, pages in groups.items()
        if isinstance(pages, list) and str(name).lower() not in GROUPS
    }


GROUPS.update(_user_groups())
WIN_W, WIN_H = 1600, 1000
LABEL_MODE = "literal"
#: the size the preview ops render at (browser.preview)
PREVIEW_W, PREVIEW_H = 800, 500
OPS = (
    "open",
    "open",
    "switch",
    "close",
    "reopen",
    "revert",
    "genie_dialog",
    "genie_ok",
    "move",
    "rotate",
    "edit_definition",
    "refresh",
    "preview_overlap",
    "library_refresh",
    "undo_redo",
    "preview_same",
    "preview_same_cold",
)


# ------------------------------------------------------------------------------ process setup
def setup_env(small_caches: bool = False) -> str:
    """Offscreen Qt with real fonts, private settings / data folders (nothing of the user's is
    read or written), no .ctF check process, no runtime."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
    os.environ["CTG_NO_CTF_PRECHECK"] = "1"
    os.environ["CTG_NO_RUNTIME"] = "1"
    tmp = tempfile.mkdtemp(prefix="ctgsoak_")
    os.environ["CTG_SETTINGS_INI"] = os.path.join(tmp, "settings.ini")
    os.environ["CTG_DATA_DIR"] = os.path.join(tmp, "data")
    if small_caches:
        from genie_recompose_sweep import apply_small_caches

        apply_small_caches()
    from ctgedit.io import reader

    reader.set_label_mode(LABEL_MODE)
    return tmp


def code_key() -> str:
    """The code that computes a baseline: every ctgedit source file's (path, size, mtime) and
    these tools'."""
    h = hashlib.sha1()
    files = [os.path.join(HERE, "session_soak.py"), os.path.join(HERE, "genie_recompose_sweep.py")]
    for d, _dirs, names in os.walk(os.path.join(ROOT, "ctgedit")):
        files += [os.path.join(d, n) for n in names if n.endswith((".py", ".json"))]
    for f in sorted(files):
        try:
            st = os.stat(f)
        except OSError:
            continue
        h.update(("%s|%d|%d;" % (os.path.relpath(f, ROOT), st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()[:12]


def rss_mb() -> float:
    """This process's working set (MB), 0 when it cannot be read."""

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.byref(pmc), pmc.cb
        )
        return round(pmc.WorkingSetSize / 2**20, 1) if ok else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


# ------------------------------------------------------------------------------ Qt driving
class Driver:
    """Runs a generator of steps inside the Qt event loop: each ``yield ms`` hands control back
    to the loop for (at least) ms milliseconds, like a user pausing between actions."""

    def __init__(self, app, gen):
        from PySide6 import QtCore

        self.app, self.gen, self.QtCore = app, gen, QtCore
        self.error = None

    def run(self) -> None:
        self.QtCore.QTimer.singleShot(0, self._tick)
        self.app.exec()

    def _tick(self) -> None:
        try:
            wait = next(self.gen)
        except StopIteration:
            self.app.quit()
            return
        except BaseException:  # noqa: BLE001 -- reported by the caller
            self.error = traceback.format_exc()
            self.app.quit()
            return
        self.QtCore.QTimer.singleShot(max(0, int(wait or 0)), self._tick)


def settle(w, min_ms: int = 250, max_ms: int = 20000):
    """Let the event loop run until the window's deferred page-open checks have run (they wait
    for the first paint, else POST_OPEN_FALLBACK_MS) and ``min_ms`` has passed."""
    t0 = time.perf_counter()
    yield 0
    while True:
        el = (time.perf_counter() - t0) * 1000
        if not w.__dict__.get("_post_open_pending") and el >= min_ms:
            return
        if el > max_ms:
            return
        yield 25


class Grab:
    """The viewport of the canvas as the user sees it: nothing selected, the page fitted, a
    few event-loop turns to paint -- then ``viewport().grab()`` (the real paint path)."""

    def __init__(self):
        self.digest, self.size, self.image = None, None, None

    def run(self, w):
        from PySide6 import QtGui, QtWidgets

        w._set_selection([])
        w.view.fit()
        for _ in range(3):
            yield 0
        vp = w.view.viewport()
        # the page only: the navigation bar / selection HUD float over the viewport as child
        # widgets (their layout is not the page's rendering)
        kids = [c for c in vp.children() if isinstance(c, QtWidgets.QWidget) and c.isVisible()]
        for c in kids:
            c.hide()
        # the amber marks on genies a background update is composing (canvas.set_pending)
        # are editor chrome too: a session grabs while one runs, a fresh editor after
        pending = list(getattr(w.view, "_pending", []) or [])
        if pending:
            w.view.set_pending([])
        # the objects an Undo / Redo brought back flash for a moment (ui/panels/history_dock):
        # chrome too, a fresh editor never undid anything
        from ctgedit.ui.panels import history_dock

        if history_dock.end_flash(w.view):
            yield 0
        try:
            img = vp.grab().toImage().convertToFormat(QtGui.QImage.Format_RGB32)
        finally:
            for c in kids:
                c.show()
            if pending:
                w.view.set_pending(pending)
        self.size = (img.width(), img.height())
        self.digest = hashlib.sha1(bytes(img.constBits())).hexdigest()[:16]
        self.image = img


def install_dialog_guards(notes: list) -> None:
    """Modal message boxes / dialogs must never block the session: answer them like a user who
    discards (Close > Discard, Question > No) and note that they appeared."""
    from PySide6 import QtWidgets

    MB = QtWidgets.QMessageBox

    def _pick(buttons, *prefer):
        for b in prefer:
            if buttons & b:
                return b
        return MB.Ok

    def question(parent, title, text, buttons=MB.Yes | MB.No, default=MB.NoButton):
        notes.append(("question", str(title), str(text)[:200]))
        return _pick(buttons, MB.Discard, MB.No, MB.Cancel)

    def _info(kind):
        def f(parent, title, text, *a, **k):
            notes.append((kind, str(title), str(text)[:200]))
            return MB.Ok

        return staticmethod(f)

    MB.question = staticmethod(question)
    MB.warning = _info("warning")
    MB.information = _info("information")
    MB.critical = _info("critical")

    def _exec(self, *a, **k):
        notes.append(("modal", type(self).__name__, str(self.windowTitle())[:120]))
        return 0

    QtWidgets.QMessageBox.exec = _exec
    QtWidgets.QDialog.exec = _exec
    # the Save Changes list (two or more unsaved pages: closing the window, Close All) is
    # answered like the one-page question: Don't Save (its exec() alone answers Cancel, and
    # a cancelled close keeps the window, so app.quit() never ends the session)
    try:
        from ctgedit.ui.dialog.save_changes import SaveChangesDialog
    except ImportError:  # (a build without the list)
        return

    def _choice(self, *a, **k):
        notes.append(("modal", type(self).__name__, str(self.windowTitle())[:120]))
        return SaveChangesDialog.DISCARD

    SaveChangesDialog.exec_choice = _choice


def make_window():
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(WIN_W, WIN_H)
    w.show()
    return app, w


# ------------------------------------------------------------------------------ baselines
def baseline_state(w, page_path: str, rec: dict, shots: str = ""):
    """Open ``page_path`` in ``w`` (a fresh window) and record the model + render as opened,
    and -- when the open checks made undo steps -- as the file holds it."""
    from genie_recompose_sweep import page_digest

    w.open_page(page_path)
    yield from settle(w, min_ms=600)
    doc = w.doc
    rec["n_open"] = len(doc._undo)
    rec["genies"] = len(doc.genie_instances())
    rec["open"] = page_digest(doc.page)
    g = Grab()
    yield from g.run(w)
    rec["open_render"], rec["render_size"] = g.digest, g.size
    if shots:
        g.image.save(os.path.join(shots, "open.png"))
    while doc.can_undo():
        w.undo()
        yield from settle(w, min_ms=100)
    rec["clean"] = page_digest(doc.page) if rec["n_open"] else rec["open"]
    if rec["n_open"]:
        g2 = Grab()
        yield from g2.run(w)
        rec["clean_render"] = g2.digest
    else:
        rec["clean_render"] = rec["open_render"]
    with open(page_path, "rb") as fh:
        rec["clean_bytes_equal"] = doc.serialize() == fh.read()


def baseline_main(page_path: str, out: str) -> int:
    """--baseline: a FRESH process computes one page's baseline (read_page model first -- the
    preview's parse --, then the window open)."""
    setup_env()
    from genie_recompose_sweep import page_digest
    from ctgedit.io import reader

    t0 = time.perf_counter()
    rec = {
        "page": page_path,
        "read": page_digest(reader.read_page(page_path)),
        "preview": preview_baseline(page_path),
    }
    notes: list = []
    install_dialog_guards(notes)
    app, w = make_window()
    shots = os.path.splitext(out)[0] + "_shots"
    os.makedirs(shots, exist_ok=True)
    drv = Driver(app, baseline_state(w, page_path, rec, shots))
    drv.run()
    rec["error"] = drv.error
    rec["notes"] = notes
    rec["seconds"] = round(time.perf_counter() - t0, 1)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    _hard_exit(0 if not drv.error else 3)


def preview_baseline(page_path: str) -> str:
    """sha1 of the picture a FRESH render process (the page browser's live render,
    browser.preview.preview_main) makes of the page at PREVIEW_W x PREVIEW_H, or 'error: ...'."""
    out = os.path.join(tempfile.mkdtemp(prefix="ctgsoak_prev_"), "base")
    env = dict(os.environ, PYTHONPATH=ROOT)
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from ctgedit.ui.browser.preview import preview_main; "
            "sys.exit(preview_main(sys.argv[1:]))",
            page_path,
            out,
            str(PREVIEW_W),
            str(PREVIEW_H),
            LABEL_MODE,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    try:
        with open(out + ".json", encoding="utf-8") as fh:
            res = json.load(fh)
    except OSError, ValueError:
        return "error: no result (exit %s) %s" % (r.returncode, r.stderr[-400:])
    return res.get("sha1") or "error: %s" % res.get("error")


def baseline_key(page_path: str, ckey: str) -> str:
    st = os.stat(page_path)
    raw = "%s|%d|%d|%s|%dx%d|%s" % (
        os.path.normcase(os.path.abspath(page_path)),
        st.st_size,
        st.st_mtime_ns,
        ckey,
        WIN_W,
        WIN_H,
        LABEL_MODE,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def baselines_for(pages, cache: str, log=print) -> dict:
    """{page path: baseline} -- cached ones reused, the others computed one at a time, each in
    its own fresh process."""
    os.makedirs(cache, exist_ok=True)
    ckey = code_key()
    out = {}
    import glob
    import shutil

    for p in pages:
        stem = "%s__%s" % (
            os.path.basename(os.path.dirname(p))[:30],
            os.path.splitext(os.path.basename(p))[0][:40].replace("!", "_"),
        )
        f = os.path.join(cache, "%s_%s.json" % (stem, baseline_key(p, ckey)))
        if os.path.isfile(f):
            with open(f, encoding="utf-8") as fh:
                out[p] = json.load(fh)
            continue
        # this page's baselines of other code / file versions are stale
        for old in glob.glob(os.path.join(cache, glob.escape(stem) + "_" + "?" * 16 + ".json")):
            with_shots = os.path.splitext(old)[0] + "_shots"
            try:
                os.remove(old)
            except OSError:
                pass
            shutil.rmtree(with_shots, ignore_errors=True)
        t0 = time.time()
        env = dict(os.environ, PYTHONPATH=ROOT)
        r = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--baseline", p, f],
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if r.returncode != 0 or not os.path.isfile(f):
            raise RuntimeError(
                "baseline of %s failed (%s):\n%s\n%s" % (p, r.returncode, r.stdout[-2000:], r.stderr[-4000:])
            )
        with open(f, encoding="utf-8") as fh:
            out[p] = json.load(fh)
        if out[p].get("error"):
            raise RuntimeError("baseline of %s: %s" % (p, out[p]["error"]))
        log("baseline %s: %.0fs" % (os.path.basename(p), time.time() - t0))
    return out


# ------------------------------------------------------------------------------ the session
class _PreviewTarget:
    """Stands in for the browser's preview pane: receives the rendered picture."""

    def __init__(self):
        self.holder = {}

    def _on_rendered(self, token, img, info, err) -> None:
        self.holder.update(img=img, info=info, err=err)


def QtCore_QObject():
    from PySide6 import QtCore

    return QtCore.QObject()


def clear_derive_caches() -> None:
    """Empty the process caches a page derivation fills on the first use of a library element
    (they are keyed by file versions, so emptying them is always safe): the next derive runs the
    cold path, as the first page of a session using those libraries does."""
    from ctgedit.format import genie_place
    from ctgedit.io import reader

    for name in ("_ART_TWINS_CACHE",):
        getattr(reader, name, {}).clear()
    reader.clear_fresh_memo()
    genie_place.clear_memo()


_SERIALS = [0]


def _serial(doc) -> int:
    """A per-document number for this session (id() is reused after a closed page is freed)."""
    n = doc.__dict__.get("_soak_serial")
    if n is None:
        _SERIALS[0] += 1
        n = doc.__dict__["_soak_serial"] = _SERIALS[0]
    return n


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class Soak:
    def __init__(self, w, pages, base, rng, steps, deadline, max_tabs, shots="", ops=OPS):
        from ctgedit.editing import ElementDocument

        self.ElementDocument = ElementDocument
        self.w, self.pages, self.base, self.rng = w, pages, base, rng
        self.steps, self.deadline, self.max_tabs = steps, deadline, max_tabs
        self.shots = shots
        self.ops = tuple(ops) or OPS
        self.checks = []  # every comparison made
        self.findings = []  # divergences / art loss / errors
        self.log = []  # (step, op, detail, seconds, rss)
        self.depth0 = {}  # _serial(doc) -> undo depth of the page as opened
        self.elements = {}  # (lib, element) -> first digest seen this session
        self.broken = {}  # _serial(doc) -> (step, failed checks) of its first divergence
        self.step = 0

    # -- helpers -------------------------------------------------------------------------
    def _page_docs(self):
        return [
            (k, e["doc"])
            for k, e in enumerate(self.w._docs)
            if not isinstance(e["doc"], self.ElementDocument) and hasattr(e["doc"], "parsed")
        ]

    def _note_open(self, doc):
        if _serial(doc) not in self.depth0:
            self.depth0[_serial(doc)] = len(doc._undo)

    def _find(self, kind, **kw):
        kw.update(step=self.step, kind=kind)
        self.findings.append(kw)

    def check(self, why: str):
        """The active page against its baseline -- when it is at a baseline state (as opened,
        or undone back to the file)."""
        w = self.w
        doc = w.doc
        if doc is None or isinstance(doc, self.ElementDocument) or not hasattr(doc, "parsed"):
            return
        path = doc.path
        base = self.base.get(_key(path))
        if base is None:
            return
        self._note_open(doc)
        depth = len(doc._undo)
        if depth == 0:
            want, want_r, state = base["clean"], base["clean_render"], "clean"
        elif depth == self.depth0.get(_serial(doc)) == base["n_open"]:
            want, want_r, state = base["open"], base["open_render"], "open"
        else:
            return
        from genie_recompose_sweep import page_digest

        got = page_digest(doc.page)
        g = Grab()
        yield from g.run(w)
        rec = {
            "step": self.step,
            "why": why,
            "page": os.path.basename(path),
            "state": state,
            "model_ok": got["model"] == want["model"],
            "drawn_ok": got["drawn"] == want["drawn"],
            "render_ok": None,
            "bytes_ok": None,
            "art": [want["art"], got["art"]],
        }
        if g.size == tuple(base["render_size"]):
            rec["render_ok"] = g.digest == want_r
        else:
            rec["render_size"] = [list(g.size), base["render_size"]]
        if state == "clean" and base.get("clean_bytes_equal"):
            with open(path, "rb") as fh:
                rec["bytes_ok"] = doc.serialize() == fh.read()
        self.checks.append(rec)
        bad = [k for k in ("model_ok", "drawn_ok", "render_ok", "bytes_ok") if rec[k] is False]
        if bad:
            roots = [r for r, h in got["roots"].items() if want["roots"].get(r) != h]
            kinds = []
            for r in roots[:6]:
                try:
                    o = doc.page.objects[int(r)]
                    kinds.append(
                        "%s %s%s"
                        % (
                            r,
                            o.kind.value,
                            (" " + str(o.raw.get("_ref") or o.raw.get("_resolved_ref") or "")).rstrip(),
                        )
                    )
                except Exception:  # noqa: BLE001
                    kinds.append(r)
            # the same document failing the same way again (its undo history cannot return to
            # the file's bytes any more) is the earlier finding carried along
            first = self.broken.get(_serial(doc))
            kind = "carried" if first is not None and set(bad) <= first[1] else "divergence"
            if first is None:
                self.broken[_serial(doc)] = (self.step, set(bad))
            extra = {"from_step": first[0]} if kind == "carried" else {}
            if kind != "carried" and ("model_ok" in bad or "drawn_ok" in bad):
                extra.update(self._model_detail(doc, want))
            self._find(
                kind,
                why=why,
                page=os.path.basename(path),
                state=state,
                failed=bad,
                objects=[want["objects"], got["objects"]],
                art=[want["art"], got["art"]],
                roots_differing=len(roots),
                roots=kinds,
                **extra,
            )
            if self.shots:
                g.image.save(
                    os.path.join(
                        self.shots,
                        "step%03d_%s.png" % (self.step, os.path.splitext(os.path.basename(path))[0][:30]),
                    )
                )

    def _model_detail(self, doc, want) -> dict:
        """Where the page's model differs from a reload of its bytes on this thread (when that
        reload is the baseline's model)."""
        from genie_recompose_sweep import page_digest
        from ctgedit import editing
        from ctgedit.format.page import parse_ctg_bytes
        from ctgedit.io import reader

        try:
            full, _der = reader.derive_page(
                parse_ctg_bytes(doc.serialize()),
                doc.path,
                records_fresh=True,
                generation=editing._LIBRARY_GENERATION[0],
            )
            return {
                "reload_matches_baseline": page_digest(full)["model"] == want["model"],
                "difference": reader.model_difference(doc.page, full)[:600],
            }
        except Exception as e:  # noqa: BLE001
            return {"difference": "reload failed: %s: %s" % (type(e).__name__, e)}

    def _genie_page(self):
        """(doc, (root, (library, element))) of a placed genie on the active page -- first
        opening a page of the group that places genies when the active one has none; (doc,
        None) when no page of the group places any."""
        w = self.w
        doc = w.doc
        ok = doc is not None and hasattr(doc, "parsed") and not isinstance(doc, self.ElementDocument)
        pick = self._genie(doc) if ok else None
        if pick is None:
            cands = [p for p in self.pages if self.base[_key(p)].get("genies")]
            if cands:
                yield from self.op_open(self.rng.choice(cands))
                doc = w.doc
                pick = self._genie(doc) if doc is not None and hasattr(doc, "parsed") else None
        return doc, pick

    def _genie(self, doc):
        try:
            insts = doc.genie_instances()
        except Exception:  # noqa: BLE001
            insts = []
        return self.rng.choice(insts) if insts else None

    def _page_path(self, rel: str) -> str:
        return rel if os.path.isabs(rel) else os.path.join(R2, rel)

    # -- operations ----------------------------------------------------------------------
    def op_open(self, path=None):
        w = self.w
        path = path or self.rng.choice(self.pages)
        docs = self._page_docs()
        already = any(os.path.normcase(d.path) == os.path.normcase(path) for _k, d in docs)
        if not already and len(w._docs) >= self.max_tabs:
            k = next((k for k, e in enumerate(w._docs) if e["doc"] is not w.doc), 0)
            w._close_doc_tab(k)
            yield from settle(w)
        w.open_page(path)
        yield from settle(w, min_ms=400)
        if w.doc is not None:
            self._note_open(w.doc)
        yield from self.check("open %s" % os.path.basename(path))
        return "open %s" % os.path.basename(path)

    def op_switch(self):
        w = self.w
        if len(w._docs) < 2:
            return (yield from self.op_open())
        cur = w.doc_tabs.currentIndex()
        k = self.rng.choice([i for i in range(len(w._docs)) if i != cur])
        w.doc_tabs.setCurrentIndex(k)
        yield from settle(w)
        yield from self.check("switch")
        return "switch to %d" % k

    def op_close(self):
        w = self.w
        if len(w._docs) < 2:
            return (yield from self.op_open())
        k = self.rng.randrange(len(w._docs))
        w._close_doc_tab(k)
        yield from settle(w)
        yield from self.check("after close")
        return "close %d" % k

    def op_reopen(self):
        w = self.w
        if w.doc is None or isinstance(w.doc, self.ElementDocument):
            return (yield from self.op_open())
        path = w.doc.path
        w._close_doc_tab(w.doc_tabs.currentIndex())
        yield from settle(w)
        return (yield from self.op_open(path))

    def op_revert(self):
        w = self.w
        if w.doc is None or isinstance(w.doc, self.ElementDocument):
            return (yield from self.op_open())
        w.revert_document(ask=False)
        yield from settle(w, min_ms=300)
        yield from self.check("revert")
        return "revert"

    def _genie_dialog(self, doc, root):
        w = self.w
        obj = doc.page.objects[root]
        w._set_selection([root])
        w._edit_genie(root, obj)
        yield 50
        return w._dialogs.get(("genie", root))

    def op_genie_dialog(self):
        w = self.w
        doc, pick = yield from self._genie_page()
        if pick is None:
            return "genie dialog: no genie"
        root, key = pick
        dlg = yield from self._genie_dialog(doc, root)
        if dlg is not None:
            dlg.reject()
        yield from settle(w)
        w._close_modeless()
        yield from self.check("genie dialog %s.%s" % key)
        return "genie dialog %s.%s" % key

    def op_genie_ok(self):
        from genie_recompose_sweep import pick_parameter, subtree_stats

        w = self.w
        doc, pick = yield from self._genie_page()
        if pick is None:
            return "genie OK: no genie"
        root, key = pick
        param = pick_parameter(doc, root, {})
        if param is None:
            return "genie OK: no parameters"
        name, old, new = param
        before = subtree_stats(doc.page, root, digests=False)
        depth = len(doc._undo)
        dlg = yield from self._genie_dialog(doc, root)
        used_dialog = False
        if dlg is not None:
            ed = dlg.params_form.editor(name) if hasattr(dlg, "params_form") else None
            if ed is not None and hasattr(ed, "setText"):
                ed.setText(new)
                used_dialog = True
            elif ed is not None and hasattr(ed, "setEditText"):
                ed.setEditText(new)
                used_dialog = True
            if used_dialog:
                dlg.accept()
            else:
                dlg.reject()
        if not used_dialog:
            if doc.set_genie_bindings(root, {name: new}):
                w._refresh()
        yield from settle(w)
        w._close_modeless()
        changed = len(doc._undo) > depth
        if changed:
            after = subtree_stats(doc.page, root, digests=False)
            if after["art"] < before["art"]:
                self._find(
                    "art_loss",
                    page=os.path.basename(doc.path),
                    element="%s.%s" % key,
                    param="%s: %r -> %r" % (name, old, new),
                    records=[before["records"], after["records"]],
                    art=[before["art"], after["art"]],
                )
            while len(doc._undo) > depth:
                w.undo()
                yield from settle(w, min_ms=100)
        yield from self.check("genie OK + undo %s.%s" % key)
        return "genie OK %s.%s %s=%r%s" % (key + (name, new, "" if changed else " (no change)"))

    def _edit_undo(self, label, fn):
        w = self.w
        doc, pick = yield from self._genie_page()
        if pick is None:
            return "%s: no genie" % label
        root, key = pick
        depth = len(doc._undo)
        w._set_selection([root])
        fn(root)
        yield from settle(w)
        while len(doc._undo) > depth:
            w.undo()
            yield from settle(w, min_ms=100)
        yield from self.check("%s + undo %s.%s" % ((label,) + key))
        return "%s %s.%s" % ((label,) + key)

    def op_move(self):
        dx, dy = self.rng.choice([(8, 0), (0, 8), (-16, 8), (3, 2)])
        return (yield from self._edit_undo("move", lambda r: self.w._on_objects_moved([r], dx, dy)))

    def op_rotate(self):
        cw = self.rng.random() < 0.5
        return (yield from self._edit_undo("rotate", lambda r: self.w._rotate(cw)))

    def op_edit_definition(self):
        from genie_recompose_sweep import page_digest

        w = self.w
        doc, pick = yield from self._genie_page()
        if pick is None:
            return "edit definition: no genie"
        root, key = pick
        d = w._definition_of(root)
        if not d:
            return "edit definition: no element"
        page_entry = next(e for e in w._docs if e["doc"] is doc)
        w._open_element(*d)
        yield from settle(w, min_ms=400)
        el = w.doc
        if isinstance(el, self.ElementDocument):
            dg = page_digest(el.page)
            k = (os.path.normcase(d[0]), d[1].lower())
            if k in self.elements and self.elements[k] != dg["model"]:
                self._find(
                    "element_divergence", element="%s.%s" % key, first=self.elements[k], now=dg["model"]
                )
            self.elements.setdefault(k, dg["model"])
            w._close_doc_tab(w.doc_tabs.currentIndex())
            yield from settle(w)
        if any(e is page_entry for e in w._docs):
            w.doc_tabs.setCurrentIndex(w._entry_index(page_entry))
            yield from settle(w)
        yield from self.check("after Edit Definition %s.%s" % key)
        return "edit definition %s.%s" % key

    def op_refresh(self):
        if self.w.doc is None:
            return (yield from self.op_open())
        self.w._refresh_view()
        yield from settle(self.w, min_ms=300)
        yield from self.check("View > Refresh")
        return "refresh"

    def op_library_refresh(self):
        try:
            self.w.library_dock.refresh()
        except Exception as e:  # noqa: BLE001
            self._find("error", op="library_refresh", error="%s: %s" % (type(e).__name__, e))
        yield from settle(self.w, min_ms=300)
        yield from self.check("library panel refresh")
        return "library refresh"

    def op_preview_overlap(self, same: bool = False, cold: bool = False):
        """A preview render overlapping an open."""
        from ctgedit.ui.browser import preview

        a = self.rng.choice(self.pages)
        b = a if same else None
        if same:
            # a page that is not open yet (the GUI must derive it)
            for k in reversed([k for k, e in enumerate(self.w._docs) if _key(e["doc"].path) == _key(a)]):
                self.w._close_doc_tab(k)
            yield from settle(self.w, min_ms=50)
        if cold:
            clear_derive_caches()
        # exactly the browser's machinery: its renderer, a token, a target (the renderer hands
        # the picture to target._on_rendered on the GUI thread)
        target = _PreviewTarget()
        rnd = preview.renderer()
        if not getattr(self, "_preview_attached", False):
            # as a pane does: after the first render one process waits for the next request
            self._preview_holder = QtCore_QObject()
            rnd.attach(self._preview_holder)
            self._preview_attached = True
        rnd.request(preview._new_token(), a, PREVIEW_W, PREVIEW_H, target)
        holder = target.holder
        name = yield from self.op_open(b)
        t0 = time.perf_counter()
        while "err" not in holder and time.perf_counter() - t0 < 120:
            yield 25
        want = self.base[_key(a)].get("preview")
        if holder.get("img") is not None:
            got = (holder.get("info") or {}).get("sha1")
            ok = got == want
            self.checks.append(
                {
                    "step": self.step,
                    "why": "preview render",
                    "page": os.path.basename(a),
                    "state": "preview",
                    "render_ok": ok,
                }
            )
            if not ok:
                self._find(
                    "divergence",
                    why="preview render process",
                    page=os.path.basename(a),
                    failed=["render_ok"],
                    concurrent_with=name,
                    difference="picture sha1 %s, a fresh render process gave %s" % (got, want),
                )
        else:
            self._find("error", op="preview_overlap", error=holder.get("err", "timeout"))
        if same and self.w.doc is not None and _key(self.w.doc.path) == _key(a):
            # is what the overlap left behind only in that document, or in the process caches
            # too? Revert re-derives the page from them
            self.w.revert_document(ask=False)
            yield from settle(self.w, min_ms=200)
            yield from self.check("revert after the preview overlap")
        return "preview %s || %s" % (os.path.basename(a), name)

    def op_preview_same(self):
        return (yield from self.op_preview_overlap(same=True))

    def op_preview_same_cold(self):
        return (yield from self.op_preview_overlap(same=True, cold=True))

    def op_undo_redo(self):
        w = self.w
        doc = w.doc
        if doc is None or isinstance(doc, self.ElementDocument) or not doc.can_undo():
            return (yield from self.op_genie_ok())
        n = len(doc._undo)
        while doc.can_undo():
            w.undo()
            yield from settle(w, min_ms=100)
        yield from self.check("undo to the file")
        while len(doc._undo) < n and doc.can_redo():
            w.redo()
            yield from settle(w, min_ms=100)
        yield from self.check("redo back")
        return "undo x%d + redo" % n

    # -- the session ---------------------------------------------------------------------
    def run(self):
        yield from self.op_open(self.pages[0])
        while self.step < self.steps and time.time() < self.deadline:
            self.step += 1
            op = self.rng.choice(self.ops)
            t0 = time.perf_counter()
            # (a crash of the process leaves this line as the last one of the log)
            print("  %3d %-16s ..." % (self.step, op), flush=True)
            try:
                detail = yield from getattr(self, "op_" + op)()
            except Exception as e:  # noqa: BLE001 -- a crash is a finding; carry on
                detail = "ERROR"
                self._find(
                    "error",
                    op=op,
                    error="%s: %s" % (type(e).__name__, e),
                    trace=traceback.format_exc()[-1500:],
                )
                yield from settle(self.w)
            self.log.append((self.step, op, str(detail), round(time.perf_counter() - t0, 2), rss_mb()))
            print(
                "  %3d %-16s %5.1fs %6.0f MB  %s" % (self.step, op, self.log[-1][3], self.log[-1][4], detail),
                flush=True,
            )


def start_allocator(pages) -> "threading.Event":
    """A worker thread that allocates like the editor's workers."""
    from ctgedit.format import pagesummary

    stop = threading.Event()

    def work():
        k = 0
        while not stop.is_set():
            junk = []
            for _ in range(3000):
                a = []
                a.append(a)
                junk.append(a)
            del junk
            try:
                pagesummary.read_page_summary(pages[k % len(pages)], want_thumbnail=True)
            except Exception:  # noqa: BLE001
                pass
            k += 1
            time.sleep(0.002)

    threading.Thread(target=work, name="soak-alloc", daemon=True).start()
    return stop


def run_soak(
    pages,
    seed: int = 1,
    steps: int = 60,
    budget_s: float = 600,
    max_tabs: int = 3,
    small_caches: bool = False,
    cache: str = "",
    shots: str = "",
    ops=OPS,
    log=print,
    alloc_thread: bool = False,
) -> dict:
    """Baselines (fresh processes, cached), then ONE window's session; returns the result
    (``alloc_thread``: with :func:`start_allocator` running through the session)."""
    pages = [p if os.path.isabs(p) else os.path.join(R2, p) for p in pages]
    pages = [p for p in pages if os.path.isfile(p)]
    if not pages:
        raise FileNotFoundError("none of the soak pages exists")
    cache = cache or os.path.join(HERE, "_soak", "baselines")
    t0 = time.time()
    base = {_key(p): b for p, b in baselines_for(pages, cache, log=log).items()}
    t_base = time.time() - t0
    setup_env(small_caches)
    notes: list = []
    install_dialog_guards(notes)
    app, w = make_window()
    if shots:
        os.makedirs(shots, exist_ok=True)
    soak = Soak(w, pages, base, random.Random(seed), steps, time.time() + budget_s, max_tabs, shots, ops)
    drv = Driver(app, soak.run())
    stop = start_allocator(pages) if alloc_thread else None
    t1 = time.time()
    drv.run()
    if stop is not None:
        stop.set()
    from ctgedit.infra import gcguard

    res = {
        "seed": seed,
        "pages": [os.path.relpath(p, R2) if p.startswith(R2) else p for p in pages],
        "small_caches": small_caches,
        "steps_run": soak.step,
        "baseline_seconds": round(t_base),
        "session_seconds": round(time.time() - t1),
        "checks": len(soak.checks),
        "checks_failed": sum(
            1
            for c in soak.checks
            if False in (c.get("model_ok"), c.get("drawn_ok"), c.get("render_ok"), c.get("bytes_ok"))
        ),
        "render_compared": sum(1 for c in soak.checks if c.get("render_ok") is not None),
        "findings": soak.findings,
        "fatal": drv.error,
        "dialogs": notes[:40],
        "log": soak.log,
        "check_list": soak.checks,
        "peak_rss_mb": max([r[4] for r in soak.log] or [0]),
        "alloc_thread": alloc_thread,
        "gc_guard": gcguard.stats(),
    }
    kinds: dict = {}
    for f in soak.findings:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    res["finding_counts"] = kinds
    res["ok"] = not res["findings"] and not res["fatal"]
    return res


def _hard_exit(code: int) -> None:
    from ctgedit.infra.paths import hard_exit

    hard_exit(code)


def main(argv=None) -> int:
    import faulthandler

    faulthandler.enable()  # an access violation prints every thread's Python stack
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--baseline"]:
        return baseline_main(argv[1], argv[2])
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--group",
        default="example",
        help="page group(s), comma-separated: %s (user groups: the role "
        "session_soak_groups in tests_corpus/local.json)" % ", ".join(GROUPS),
    )
    ap.add_argument("--pages", default="", help="comma-separated pages instead of a group")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--budget-s", type=float, default=600)
    ap.add_argument("--max-tabs", type=int, default=3)
    ap.add_argument("--small-caches", action="store_true")
    ap.add_argument(
        "--alloc-thread",
        action="store_true",
        help="a worker thread allocating through the session (the GC crash class; "
        "CTG_GC_GUARD=0 = without the GUI-thread-only collector)",
    )
    ap.add_argument(
        "--ops",
        default="",
        help="comma-separated operations to draw from "
        "(repeat one to weight it; default: all): %s" % ", ".join(sorted(set(OPS))),
    )
    ap.add_argument("--cache", default="")
    ap.add_argument("--shots", default="", help="save the viewport of every divergence here")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if a.pages:
        pages = [p.strip() for p in a.pages.split(",") if p.strip()]
    else:
        pages = []
        for g in a.group.split(","):
            g = g.strip()
            if g.lower() not in GROUPS:
                ap.error(
                    "unknown group %r (known: %s; user groups come from the role "
                    "session_soak_groups in tests_corpus/local.json)" % (g, ", ".join(GROUPS))
                )
            pages += GROUPS[g.lower()]
    ops = tuple(o.strip() for o in a.ops.split(",") if o.strip()) or OPS
    unknown = sorted(set(ops) - set(OPS))
    if unknown:
        ap.error("unknown operation(s) %s" % ", ".join(unknown))
    res = run_soak(
        pages,
        seed=a.seed,
        steps=a.steps,
        budget_s=a.budget_s,
        max_tabs=a.max_tabs,
        small_caches=a.small_caches,
        cache=a.cache,
        ops=ops,
        shots=a.shots,
        alloc_thread=a.alloc_thread,
    )
    out = a.out or os.path.join(
        HERE,
        "_soak",
        "soak_%s_s%d_%s.json"
        % (a.group.replace(",", "+") if not a.pages else "pages", a.seed, time.strftime("%Y%m%d_%H%M%S")),
    )
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    brief = {
        k: res[k]
        for k in (
            "seed",
            "steps_run",
            "checks",
            "checks_failed",
            "finding_counts",
            "render_compared",
            "baseline_seconds",
            "session_seconds",
            "peak_rss_mb",
            "gc_guard",
            "ok",
        )
    }
    print(json.dumps(brief))
    for f in res["findings"][:30]:
        print("FINDING", json.dumps(f, default=str)[:600])
    if res["fatal"]:
        print("FATAL", res["fatal"][-3000:])
    print("->", out)
    _hard_exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
