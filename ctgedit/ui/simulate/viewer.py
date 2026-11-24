"""The Simulate window's viewer side of a simulation."""

from __future__ import annotations

import base64
import os
import threading
import weakref
from typing import Any, Dict, List, Optional, Tuple

from ...infra import logsetup
from ...sim import client as C

log = logsetup.get("sim")


def _note_failed(what: str, err: Any) -> None:
    """A request sent without a caller waiting: a failure goes to the log, not nowhere (a
    lost connection is said by the session itself)."""
    if err is not None and not isinstance(err, C.ConnectionLost):
        log.warning("ctsim: %s failed: %s", what, err)


def page_key(page: Any) -> str:
    """A page name as the runtime compares it: without case, a '!' popup prefix counts."""
    return str(page or "").strip().lstrip("!").lower()


class ViewerServices:
    """The modelWanted / dialog / imageWanted events of one session, kept for the window's
    tick."""

    def __init__(self, session: C.Session):
        self._session = weakref.ref(session)
        self._lock = threading.Lock()
        self._wanted: List[Dict[str, Any]] = []
        self._dialogs: List[Tuple[str, Dict[str, Any]]] = []
        self._images: List[Dict[str, Any]] = []
        # the recoloured pictures WinCopy put on the clipboard (bitmap file bytes)
        self._clips: List[bytes] = []
        # window -> the page whose model went to it (by a tab or an answered modelWanted)
        self._sent: Dict[int, str] = {}
        self.users = 0

    @property
    def session(self) -> Optional[C.Session]:
        return self._session()

    # -- the reader thread ------------------------------------------------------------------
    def on_event(self, name: str, body: Any) -> None:
        ev = name[6:] if name.startswith("ctsim/") else name
        if ev not in ("modelWanted", "dialog", "dialogClosed", "imageWanted"):
            return
        body = dict(body) if isinstance(body, dict) else {}
        with self._lock:
            if ev == "imageWanted":
                self._images.append(body)
            elif ev == "modelWanted":
                try:
                    w = int(body.get("window"))
                except TypeError, ValueError:
                    return
                body["window"] = w
                # the window shows (again) a page: its drawing is wanted again, whatever went
                # to it before
                self._sent.pop(w, None)
                self._wanted = [x for x in self._wanted if x["window"] != w]
                self._wanted.append(body)
            else:
                self._dialogs.append((ev, body))

    @property
    def closed(self) -> bool:
        """The connection ended (nobody waits for its dialogs any more)."""
        s = self.session
        return s is None or not s.alive

    # -- the GUI thread ------------------------------------------------------------------
    def take_models(self) -> List[Dict[str, Any]]:
        """The modelWanted bodies not answered yet ({window, page, path}), once."""
        with self._lock:
            out, self._wanted = self._wanted, []
            return out

    def take_dialogs(self) -> List[Tuple[str, Dict[str, Any]]]:
        """('dialog' | 'dialogClosed', body) in arrival order, once."""
        with self._lock:
            out, self._dialogs = self._dialogs, []
            return out

    def take_images(self) -> List[Dict[str, Any]]:
        """The imageWanted bodies ({id, window, page, function, xScale, yScale, clipboard}),
        once."""
        with self._lock:
            out, self._images = self._images, []
            return out

    def take_clipboard(self) -> List[bytes]:
        """The pictures for the clipboard (bitmap files) the simulator sent back, once."""
        with self._lock:
            out, self._clips = self._clips, []
            return out

    def send_image(self, image_id: Any, bmp: Optional[bytes], clipboard: bool = False) -> bool:
        """``ctsim/windowImage {id, bmp}`` (a bitmap file, base64), or ``{id, none: true}``
        when ``bmp`` is None (async). With ``clipboard``, the recoloured picture of the answer
        goes to :meth:`take_clipboard`. False when the session is gone."""
        s = self.session
        if s is None or not s.alive:
            return False
        args: Dict[str, Any] = {"id": image_id}
        if bmp is None:
            args["none"] = True
        else:
            args["bmp"] = base64.b64encode(bmp).decode("ascii")

        def done(body, err):
            if err is not None:
                _note_failed("the picture of window request %s" % image_id, err)
                return
            data = body.get("bmp") if clipboard and isinstance(body, dict) else None
            if isinstance(data, str) and data:
                try:
                    raw = base64.b64decode(data)
                except ValueError as e:
                    log.warning("ctsim: the clipboard picture was not base64: %s", e)
                    return
                with self._lock:
                    self._clips.append(raw)

        try:
            s.request_async("windowImage", args, callback=done, timeout=C.LOAD_TIMEOUT)
        except C.CtsimError as e:
            log.info("ctsim: the picture for request %s did not go: %s", image_id, e)
            return False
        return True

    def model_wanted_or_sent(self, window: Optional[int], page: str) -> bool:
        """The engine asked for (or got) the drawing of ``page`` in ``window``: a tab sends
        no model of its own for it (it would be the same drawing a second time, late)."""
        if window is None:
            return False
        key = page_key(page)
        with self._lock:
            if self._sent.get(int(window)) == key:
                return True
            return any(
                x["window"] == int(window) and (not key or page_key(x.get("page")) == key)
                for x in self._wanted
            )

    def note_model_sent(self, window: int, page: str) -> None:
        with self._lock:
            self._sent[int(window)] = page_key(page)

    def send_model(self, window: int, page: str, model: dict, done=None) -> bool:
        """``ctsim/pageModel {window, model}`` (async; ``done(body, err)`` on the reader
        thread). False when the session is gone."""
        s = self.session
        if s is None or not s.alive:
            return False
        self.note_model_sent(window, page)
        try:
            s.request_async(
                "pageModel",
                {"window": int(window), "model": model},
                callback=done or (lambda body, err, p=page: _note_failed("the drawing of %s" % p, err)),
                timeout=C.LOAD_TIMEOUT,
            )
        except C.CtsimError as e:
            log.info("ctsim: the drawing of %s for window %s did not go: %s", page, window, e)
            return False
        return True

    def send_no_model(self, window: int, page: str) -> bool:
        """``ctsim/pageModel {window, none: true}``: there is no drawing
        for the page (its file is not there), so the simulator starts the page at once instead
        of waiting for one. False when the session is gone or older."""
        s = self.session
        if s is None or not s.alive:
            return False
        self.note_model_sent(window, page)
        try:
            s.request_async(
                "pageModel",
                {"window": int(window), "none": True},
                callback=lambda body, err, p=page: _note_failed("'no drawing' of %s" % p, err),
                timeout=C.LOAD_TIMEOUT,
            )
        except C.CtsimError as e:
            log.info("ctsim: 'no drawing' of %s for window %s did not go: %s", page, window, e)
            return False
        return True

    def answer(self, dialog_id: Any, **kw) -> bool:
        """``ctsim/answer {id, choice?, ok?, text?, values?}`` (async)."""
        s = self.session
        if s is None or not s.alive:
            return False
        try:
            s.request_async(
                "answer",
                C.Session.answer_args(dialog_id, **kw),
                callback=lambda body, err, d=dialog_id: _note_failed("the answer to dialog %s" % d, err),
            )
        except C.CtsimError as e:
            log.info("ctsim: the answer to dialog %s did not go: %s", dialog_id, e)
            return False
        return True

    def search_dirs(self) -> List[str]:
        """The project folders the simulation reads, in its search order (hello 'unit')."""
        s = self.session
        if s is None:
            return []
        return [d for _n, d in s.unit if d]


def find_page_file(page: str, path: str = "", dirs=()) -> str:
    """The drawing file of ``page``: ``path`` when it exists, else ``<dir>\\<page>.ctg`` of
    the first folder of ``dirs`` that has it ('' when none)."""
    if path and os.path.isfile(path):
        return path
    name = str(page or "").strip()
    if not name:
        return ""
    for d in dirs:
        for cand in (name, name.lstrip("!"), "!" + name.lstrip("!")):
            p = os.path.join(d, cand + ".ctg")
            if os.path.isfile(p):
                return p
    return ""


#: the services of every live session (one each, made when a tab connects)
_SERVICES: "weakref.WeakKeyDictionary[C.Session, ViewerServices]" = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()


def services_for(session: Optional[C.Session]) -> Optional[ViewerServices]:
    """The session's services while a tab uses them (:func:`attach`), else None."""
    if session is None:
        return None
    with _LOCK:
        return _SERVICES.get(session)


def attach(session: C.Session) -> ViewerServices:
    """A tab uses the session: its services, made (and the event listener registered) by the
    first tab. Call it right after the session is acquired, before a page opens, so no
    modelWanted is missed; :func:`detach` when the tab lets the session go (the last one
    removes the listeners: a shared session keeps nothing of a closed Simulate window)."""
    with _LOCK:
        svc = _SERVICES.get(session)
        new = svc is None
        if new:
            svc = ViewerServices(session)
            _SERVICES[session] = svc
        svc.users += 1
    if new:
        session.add_listener(svc.on_event)
    return svc


def detach(session: Optional[C.Session]) -> None:
    if session is None:
        return
    with _LOCK:
        svc = _SERVICES.get(session)
        if svc is None:
            return
        svc.users -= 1
        if svc.users > 0:
            return
        _SERVICES.pop(session, None)
    session.remove_listener(svc.on_event)
