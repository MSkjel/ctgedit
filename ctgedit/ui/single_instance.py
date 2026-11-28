"""One editor per user: a second launch hands its pages to the running one."""

from __future__ import annotations

from ..infra.paths import path_key

import contextlib
import getpass
import hashlib
import json
import os
import sys
from typing import List, Optional

from PySide6 import QtCore, QtNetwork


def _session_id() -> int:
    """The Windows logon session of this process (0 elsewhere). Pipe names
    are machine-wide, so without it a launch in one session (an RDP login)
    would hand its pages to the same user's editor in another."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        sid = ctypes.c_ulong(0)
        if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
            return int(sid.value)
    except Exception:  # noqa: BLE001  # swallow-ok: feature probe, no session id then
        pass
    return 0


def server_name() -> str:
    """Per user, logon session and settings store: an editor run against an
    isolated settings file (tests, ``CTG_SETTINGS_INI``) never meets the
    user's, and each session of the user has its own editor."""
    try:
        who = getpass.getuser()
    except Exception:  # noqa: BLE001  # swallow-ok: no user name, the environment's then
        who = os.environ.get("USERNAME", "user")
    ini = os.environ.get("CTG_SETTINGS_INI")
    store = path_key(ini) if ini else "registry"
    digest = hashlib.sha1(("%s|%s|%d" % (who.lower(), store, _session_id())).encode("utf-8")).hexdigest()
    return "ctgedit-" + digest[:16]


class StartupLock:
    """Held from "is an editor running?" until this process listens."""

    def __init__(self, name: Optional[str] = None):
        path = os.path.join(QtCore.QDir.tempPath(), (name or server_name()) + ".lock")
        self._lock = QtCore.QLockFile(path)
        self._lock.setStaleLockTime(30000)

    def acquire(self, timeout_ms: int = 10000) -> bool:
        return self._lock.tryLock(timeout_ms)

    def release(self) -> None:
        if self._lock.isLocked():
            self._lock.unlock()


def _allow_foreground() -> None:
    """Let the running editor take the foreground: Windows only lets the
    process the user just started (this one) grant that."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_ulong(0xFFFFFFFF))  # ASFW_ANY
        except Exception:  # noqa: BLE001  # swallow-ok: a convenience, the window may stay behind
            pass


def forward(files: List[str], name: Optional[str] = None, timeout_ms: int = 3000) -> bool:
    """Hand ``files`` to a running editor. True when one took them (this
    process should then exit), False when none is running."""
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    _allow_foreground()
    payload = json.dumps({"files": [os.path.abspath(f) for f in files]})
    sock.write(payload.encode("utf-8") + b"\n")
    if not sock.waitForBytesWritten(timeout_ms):
        sock.abort()
        return False
    # the reply only confirms the hand-over; an editor busy opening a large
    # page reads the request once it is free again
    reply = b""
    while b"\n" not in reply and sock.waitForReadyRead(timeout_ms):
        reply += bytes(sock.readAll())
    sock.disconnectFromServer()
    return True


class InstanceServer(QtCore.QObject):
    """The running editor's end: emits the file list of every later launch."""

    filesRequested = QtCore.Signal(list)

    def __init__(self, name: Optional[str] = None, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self.name = name or server_name()
        self._server = QtNetwork.QLocalServer(self)
        self._server.setSocketOptions(QtNetwork.QLocalServer.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)
        self._buffers: dict = {}

    def listen(self) -> bool:
        if self._server.listen(self.name):
            return True
        # a socket file left by a crashed run (not Windows: pipes vanish)
        QtNetwork.QLocalServer.removeServer(self.name)
        return self._server.listen(self.name)

    def close(self) -> None:
        for sock in list(self._buffers):
            with contextlib.suppress(RuntimeError, TypeError):
                sock.readyRead.disconnect(self._ready_signal)
                sock.disconnected.disconnect(self._disconnected_signal)
                sock.abort()
        self._buffers.clear()
        self._server.close()

    # the socket signals go to bound slots of THIS object (not lambdas): Qt
    # drops a receiver's connections before it deletes its children, so the
    # sockets dying with the server never call back into a half-destroyed one
    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(self._ready_signal)
            sock.disconnected.connect(self._disconnected_signal)
            if sock.bytesAvailable():
                self._on_ready(sock)  # arrived before readyRead was connected

    @QtCore.Slot()
    def _ready_signal(self) -> None:
        sock = self.sender()
        if sock is not None:
            self._on_ready(sock)

    @QtCore.Slot()
    def _disconnected_signal(self) -> None:
        sock = self.sender()
        if sock is not None:
            self._drop(sock)

    def _drop(self, sock) -> None:
        # a sender that gave up waiting for the reply (this editor was busy)
        # still left its request in the pipe
        if sock in self._buffers:
            with contextlib.suppress(RuntimeError):
                self._on_ready(sock, closing=True)
        self._buffers.pop(sock, None)
        with contextlib.suppress(RuntimeError):
            sock.deleteLater()

    def _on_ready(self, sock, closing: bool = False) -> None:
        if sock not in self._buffers:
            return
        self._buffers[sock] += bytes(sock.readAll())
        data = self._buffers[sock]
        if b"\n" not in data:
            return
        line = data.split(b"\n", 1)[0]
        self._buffers[sock] = b""
        try:
            msg = json.loads(line.decode("utf-8"))
            files = [str(f) for f in msg.get("files", []) if f]
        except ValueError, AttributeError:  # (UnicodeDecodeError is a ValueError)
            files = None
        if not closing:
            sock.write(b"ok\n" if files is not None else b"error\n")
            sock.flush()
        if files is not None:
            self.filesRequested.emit(files)
