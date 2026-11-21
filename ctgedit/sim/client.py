"""Client for the Cicode simulator."""

from __future__ import annotations

import atexit
import collections
import dataclasses
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..infra import gcguard, logsetup, paths

log = logsetup.get("sim")

#: the ctsim/* protocol version this client speaks (sent in ctsim/hello)
PROTOCOL = "ctsim/0"  # the server's own name (Host/SimServer.cs Protocol)
CLIENT = "ctgedit"
#: the setting that names ctsim.exe (read by the Qt side, passed to find_engine)
SETTING = "sim/ctsim_path"
#: environment overrides: ctsim.exe, the discovery folder (the same variable the server
#: reads, so a server this client starts writes where the client looks), and "do not use the
#: simulator"
PATH_ENV = "CTG_CTSIM_PATH"
SERVERS_ENV = "CICODESIM_SERVERS"  # the engine's own name (Host/Discovery.cs)
DISABLE_ENV = "CTG_NO_SIM_ENGINE"

EXE_NAME = "ctsim.exe"
DLL_NAME = "CicodeSim.Engine.dll"
CONFIG_NAME = "ctsim.exe.config"
#: the engine checkout's net48 build output, relative to the repo root
CHECKOUT_BUILD = ("external", "cicode-sim", "engine", "src", "ctsim", "bin", "Release", "net48")

#: request timeouts (seconds)
DEFAULT_TIMEOUT = 10.0
HELLO_TIMEOUT = 5.0
LOAD_TIMEOUT = 300.0
TAGS_TIMEOUT = 30.0
CONNECT_TIMEOUT = 5.0
#: how long a started server may take to announce itself (it loads the project first)
START_TIMEOUT = 120.0
#: a started server nobody uses any more is stopped after this many seconds
LINGER_S = 30.0
#: tags per ctsim/tags request
TAG_PAGE = 500
#: the largest message accepted (the engine's own limit for what it sends)
MAX_BODY = 64 << 20
#: lines of a started server's output kept for messages
OUTPUT_LINES = 200

CREATE_NO_WINDOW = 0x08000000

REQUESTS = (
    "hello",
    "load",
    "tags",
    "subscribe",
    "setTag",
    "call",
    "clock",
    "scenario",
    "openPage",
    "pageState",
    "input",
    "closePage",
    "pageModel",
    "windows",
    "answer",
    "alarms",
    "equipment",
    "setAlarm",
    "setEquipment",
    "navigate",
)
EVENTS = (
    "tagsChanged",
    "log",
    "notSimulated",
    "taskStarted",
    "taskExited",
    "pageChanged",
    "windowsChanged",
    "clockChanged",
    "scenarioFinished",
    "modelWanted",
    "dialog",
    "dialogClosed",
    "alarmsChanged",
)
#: the engine's protocol revision this client needs (engine/PROTOCOL.md 'Versions'): a
#: server below it is not attached to, and a started one is refused (open_session)
PROTOCOL_REVISION = 29

#: what this viewer does for the server (hello): it sends the drawing of a page Cicode opens
#: when the server asks (ctsim/modelWanted), and it shows popup menus and Message / Input /
#: Form dialogs (ctsim/dialog, answered with ctsim/answer)
VIEWER_FEATURES = {"models": True, "dialogs": True}
#: every event kind a hello can ask for (engine/PROTOCOL.md ctsim/hello 'events')
#: ('debug': ctsim/debugPaused, page code the debugger stopped holds the simulation)
EVENT_KINDS = ("log", "task", "notSimulated", "clock", "scenario", "page", "options", "alarms", "debug")
#: the event kinds a viewer asks for (None = all); the Simulate window leaves 'task' out
#: while its Outputs panel does not show tasks (an SA alarm page can run thousands of tasks
#: a second)
viewer_events: Optional[Tuple[str, ...]] = None
#: the alarm states ctsim/setAlarm takes as words
ALARM_STATES = ("on", "on_ack", "off_unack", "off", "disabled")


def protocol_revision(info) -> int:
    """The protocolRevision of a hello body, a discovery entry's data or a --version --json
    answer; 0 when it reports none."""
    try:
        return int((info or {}).get("protocolRevision") or 0)
    except TypeError, ValueError, AttributeError:
        return 0


def build_text(info) -> str:
    """'commit f69e50ea61cd, built 2026-10-02 19:59 UTC' from a hello body ('' when it has
    neither)."""
    info = info or {}
    bits = []
    commit = str(info.get("commit") or "")
    if commit:
        bits.append("commit " + commit[:12])
    built = str(info.get("buildTime") or "")
    if built:
        bits.append("built " + built.replace("T", " ").rstrip("Z")[:16] + " UTC")
    return ", ".join(bits)


def process_exe(pid: int) -> str:
    """The executable of process ``pid``, '' when it cannot be read."""
    if os.name != "nt" or not pid:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            n = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return buf.value
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 -- information only
        pass
    return ""


def file_date(path: str) -> str:
    """'2026-10-02 12:30' (the file's modified time), '' when there is none."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
    except OSError, ValueError, TypeError:
        return ""


#: the page scan the clock bar counts in (the engine reports time, not scans)
SCAN_SECONDS = 0.25
#: the Simulate canvas's input kinds -> the engine's
INPUT_KINDS = {"double": "dblclick"}


def command(name: str) -> str:
    """'tags' -> 'ctsim/tags' (a full name is kept)."""
    return name if "/" in name else "ctsim/" + name


# -- errors -------------------------------------------------------------------------------------
class CtsimError(Exception):
    """Something about the simulator failed; ``str()`` is a plain sentence for the user."""


class EngineNotFound(CtsimError):
    """No ctsim.exe: ``searched`` = the places looked at, in order."""

    def __init__(self, message: str, searched: Sequence[str] = ()):
        super().__init__(message)
        self.searched = list(searched)


class ServerUnavailable(CtsimError):
    """A server could not be started or reached (a start that failed, a server that does not
    answer ctsim/hello)."""


class ConnectionLost(CtsimError):
    """The connection closed (the server ended, crashed, or the client closed it)."""


class RequestTimeout(CtsimError):
    def __init__(self, cmd: str, timeout: float):
        super().__init__(
            "The Cicode simulator did not answer the %s request within %g s." % (_short_command(cmd), timeout)
        )
        self.command = cmd
        self.timeout = timeout


class RequestFailed(CtsimError):
    """The server answered ``success: false``: ``message`` = its text."""

    def __init__(self, cmd: str, message: str, body: Any = None):
        super().__init__(message or "%s failed." % _short_command(cmd))
        self.command = cmd
        self.message = message
        self.body = body


class NotAvailable(RequestFailed):
    """The server does not know this request (an engine build that does not have it yet)."""


class FramingError(CtsimError):
    pass


def _short_command(cmd: str) -> str:
    return cmd[6:] if cmd.startswith("ctsim/") else cmd


#: the engine's answer to a request it does not have (Host/SimServer.cs: "unknown request
#: 'ctsim/x'", also "unknown request 'x': send ctsim/hello ... first")
_UNKNOWN = re.compile(r"^\s*unknown request\b", re.I)


def _is_unknown_request(message: str, body: Any = None) -> bool:
    """Is this failure the server saying it doesn't know the request?"""
    if isinstance(body, dict) and body.get("notImplemented") is True:
        return True
    return bool(_UNKNOWN.match(message or ""))


# -- framing ------------------------------------------------------------------------------------
def encode(message: dict) -> bytes:
    """One DAP frame: ``Content-Length: N`` CR LF CR LF then N bytes of UTF-8 JSON."""
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


class FrameParser:
    """A push parser of DAP frames (any split, several frames in one chunk). Header names
    are case-insensitive, other headers are ignored, a bare LF ends a line too."""

    def __init__(self, max_body: int = MAX_BODY):
        self.max_body = max_body
        self._buf = bytearray()
        self._length = -1

    def at_frame_start(self) -> bool:
        return not self._buf and self._length < 0

    def feed(self, data: bytes) -> List[bytes]:
        self._buf += data
        out: List[bytes] = []
        while True:
            if self._length < 0:
                end = self._header_end()
                if end is None:
                    if len(self._buf) > 65536:
                        raise FramingError("DAP header too long")
                    return out
                head = bytes(self._buf[: end[0]])
                del self._buf[: end[1]]
                self._length = self._content_length(head)
            if len(self._buf) < self._length:
                return out
            out.append(bytes(self._buf[: self._length]))
            del self._buf[: self._length]
            self._length = -1

    def _header_end(self):
        crlf = self._buf.find(b"\r\n\r\n")
        lf = self._buf.find(b"\n\n")
        ends = [(p, p + 4) for p in (crlf,) if p >= 0] + [(p, p + 2) for p in (lf,) if p >= 0]
        return min(ends) if ends else None

    def _content_length(self, head: bytes) -> int:
        length = -1
        for line in head.replace(b"\r\n", b"\n").split(b"\n"):
            if not line.strip():
                continue
            name, sep, value = line.partition(b":")
            if not sep or not name.strip():
                raise FramingError("bad DAP header line: %r" % line[:60])
            if name.strip().lower() == b"content-length":
                v = value.strip()
                if not v.isdigit():
                    raise FramingError("bad Content-Length: %r" % v[:60])
                length = int(v)
        if length < 0:
            raise FramingError("DAP header without Content-Length")
        if length > self.max_body:
            raise FramingError("DAP body of %d bytes is over the limit of %d" % (length, self.max_body))
        return length


# -- transports -----------------------------------------------------------------------------
ERROR_FILE_NOT_FOUND = 2
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_MORE_DATA = 234
ERROR_PIPE_BUSY = 231
ERROR_SEM_TIMEOUT = 121
ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995
ERROR_PIPE_CONNECTED = 535
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
_EOF_ERRORS = (ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED, ERROR_OPERATION_ABORTED)
_POLL_MS = 200


class PipeStream:
    """A duplex stream over an overlapped named pipe handle."""

    def __init__(self, handle: int, name: str = ""):
        self._h = handle
        self.name = name
        self._closed = False
        self._wlock = threading.Lock()
        self._state = threading.Lock()
        self._read_ov = None
        self._handle_open = True

    @property
    def closed(self) -> bool:
        return self._closed

    def read(self, timeout: Optional[float] = None, size: int = 65536) -> Optional[bytes]:
        import _winapi

        if self._closed:
            return b""
        with self._state:
            if self._closed:
                return b""
            try:
                ov, err = _winapi.ReadFile(self._h, size, overlapped=True)
            except OSError as e:
                if getattr(e, "winerror", 0) in _EOF_ERRORS:
                    return b""
                raise
            self._read_ov = ov
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            if err == ERROR_IO_PENDING:
                while True:
                    wait = _POLL_MS
                    if deadline is not None:
                        wait = max(0, min(wait, int((deadline - time.monotonic()) * 1000)))
                    res = _winapi.WaitForMultipleObjects([ov.event], False, wait)
                    if res == WAIT_OBJECT_0:
                        break
                    if self._closed or (deadline is not None and time.monotonic() >= deadline):
                        try:
                            ov.cancel()
                        except OSError:
                            pass
                        try:
                            n, err = ov.GetOverlappedResult(True)
                        except OSError:
                            return b"" if self._closed else None
                        if n:
                            # the read completed while it was being cancelled
                            return bytes(ov.getbuffer())
                        return b"" if self._closed else None
            try:
                n, err = ov.GetOverlappedResult(True)
            except OSError as e:
                if getattr(e, "winerror", 0) in _EOF_ERRORS:
                    return b""
                raise
            if err not in (0, ERROR_MORE_DATA):
                return b""
            data = bytes(ov.getbuffer())
            return data if data else (b"" if self._closed else None)
        finally:
            with self._state:
                self._read_ov = None
                if self._closed:
                    self._close_handle()

    def write(self, data: bytes, timeout: float = 30.0, hold: Optional[Callable[[], bool]] = None) -> None:
        """``hold()`` True = the server is held by a debugger (it reads nothing until it goes
        on): the write waits on, the timeout starts again."""
        import _winapi

        view = memoryview(data)
        with self._wlock:
            while len(view):
                if self._closed:
                    raise ConnectionLost("The connection to the Cicode simulator is closed.")
                try:
                    ov, err = _winapi.WriteFile(self._h, bytes(view), overlapped=True)
                except OSError as e:
                    raise ConnectionLost("The Cicode simulator closed the connection (%s)." % e)
                deadline = time.monotonic() + timeout
                try:
                    if err == ERROR_IO_PENDING:
                        while True:
                            res = _winapi.WaitForMultipleObjects([ov.event], False, _POLL_MS)
                            if res == WAIT_OBJECT_0:
                                break
                            if not self._closed and hold is not None and hold():
                                deadline = time.monotonic() + timeout
                            if self._closed or time.monotonic() >= deadline:
                                try:
                                    ov.cancel()
                                except OSError:
                                    pass
                                break
                    n, err = ov.GetOverlappedResult(True)
                except OSError as e:
                    raise ConnectionLost("The Cicode simulator closed the connection (%s)." % e)
                if err != 0 or n <= 0:
                    raise ConnectionLost("The Cicode simulator does not read its messages.")
                view = view[n:]

    def close(self) -> None:
        with self._state:
            if self._closed:
                return
            self._closed = True
            ov = self._read_ov
            if ov is not None:
                try:
                    ov.cancel()  # the reader closes the handle when it returns
                except OSError:
                    pass
                return
            self._close_handle()

    def _close_handle(self) -> None:
        if not self._handle_open:
            return
        self._handle_open = False
        import _winapi

        # a write in flight on another thread sees _closed within one poll and cancels its
        # I/O: the handle is closed after it let go (never under a pending overlapped write)
        got = self._wlock.acquire(timeout=2.0)
        try:
            _winapi.CloseHandle(self._h)
        except OSError:
            pass
        finally:
            if got:
                self._wlock.release()


def open_pipe(path: str, timeout: float = CONNECT_TIMEOUT) -> PipeStream:
    """Connect to the named pipe ``path`` (``\\\\.\\pipe\\name``; a bare name is completed).
    No such pipe = ServerUnavailable at once; every instance busy = wait up to ``timeout``."""
    import _winapi

    if not path.lower().startswith("\\\\.\\pipe\\"):
        path = "\\\\.\\pipe\\" + path
    deadline = time.monotonic() + timeout
    while True:
        try:
            h = _winapi.CreateFile(
                path,
                _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0,
                _winapi.NULL,
                _winapi.OPEN_EXISTING,
                _winapi.FILE_FLAG_OVERLAPPED,
                _winapi.NULL,
            )
            return PipeStream(h, path)
        except OSError as e:
            code = getattr(e, "winerror", 0)
            left = deadline - time.monotonic()
            if code == ERROR_PIPE_BUSY and left > 0:
                try:
                    _winapi.WaitNamedPipe(path, max(1, int(min(left, 1.0) * 1000)))
                except OSError:
                    pass
                continue
            if code == ERROR_FILE_NOT_FOUND:
                raise ServerUnavailable("No Cicode simulator is listening on %s." % path)
            raise ServerUnavailable("Cannot connect to the Cicode simulator on %s (%s)." % (path, e))


class SocketStream:
    """The same interface over a connected socket (the TCP fallback, tests)."""

    def __init__(self, sock, name: str = ""):
        self._s = sock
        self.name = name
        self._closed = False
        self._wlock = threading.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    def read(self, timeout: Optional[float] = None, size: int = 65536) -> Optional[bytes]:
        # select, not settimeout: the socket's timeout is shared with the writer thread (a
        # read's short poll would cut a write that waits for the server short)
        import select

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._closed:
                return b""
            wait = _POLL_MS / 1000.0
            if deadline is not None:
                wait = max(0.0, min(wait, deadline - time.monotonic()))
            try:
                ready, _w, _x = select.select([self._s], [], [], wait)
                if ready:
                    return self._s.recv(size)
            except BlockingIOError, InterruptedError:
                pass
            except OSError, ValueError:
                return b""
            if deadline is not None and time.monotonic() >= deadline:
                return None

    def write(self, data: bytes, timeout: float = 30.0, hold: Optional[Callable[[], bool]] = None) -> None:
        import select

        view = memoryview(data)
        with self._wlock:
            deadline = time.monotonic() + timeout
            while len(view):
                if self._closed:
                    raise ConnectionLost("The connection to the Cicode simulator is closed.")
                try:
                    _r, ready, _x = select.select([], [self._s], [], _POLL_MS / 1000.0)
                    if not ready:
                        if hold is not None and hold():
                            deadline = time.monotonic() + timeout
                        if time.monotonic() >= deadline:
                            raise ConnectionLost("The Cicode simulator does not read its messages.")
                        continue
                    n = self._s.send(view)
                except BlockingIOError, InterruptedError:
                    continue
                except (OSError, ValueError) as e:
                    raise ConnectionLost("The Cicode simulator closed the connection (%s)." % e)
                view = view[n:]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        import socket

        try:
            self._s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._s.close()
        except OSError:
            pass


def open_tcp(address: str, timeout: float = CONNECT_TIMEOUT) -> SocketStream:
    import socket

    host, _sep, port = str(address).rpartition(":")
    try:
        s = socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout)
        s.setblocking(False)  # SocketStream waits with select
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (OSError, ValueError) as e:
        raise ServerUnavailable("Cannot connect to the Cicode simulator on %s (%s)." % (address, e))
    return SocketStream(s, address)


def connect_stream(transport: str, address: str, timeout: float = CONNECT_TIMEOUT):
    transport = (transport or "pipe").lower()
    if transport == "pipe":
        if os.name != "nt":
            raise ServerUnavailable("Named pipes need Windows.")
        return open_pipe(address, timeout)
    if transport == "tcp":
        return open_tcp(address, timeout)
    raise ServerUnavailable(
        "The Cicode simulator listens on a %s socket, which ctgedit cannot reach." % transport
    )


# -- the connection -------------------------------------------------------------------------
class _Pending:
    __slots__ = ("seq", "command", "event", "body", "error", "callback", "deadline", "timeout")

    def __init__(self, seq: int, cmd: str, timeout: Optional[float]):
        self.seq = seq
        self.command = cmd
        self.event = threading.Event()
        self.body: Any = None
        self.error: Optional[CtsimError] = None
        self.callback: Optional[Callable[[Any, Optional[CtsimError]], None]] = None
        self.timeout = timeout
        self.deadline = None if timeout is None else time.monotonic() + timeout

    def done(self) -> bool:
        return self.event.is_set()


class PendingRequest:
    """An async request: ``wait(timeout)`` gives its body or raises its error."""

    def __init__(self, conn: "Connection", pending: _Pending):
        self._conn = conn
        self._p = pending

    @property
    def command(self) -> str:
        return self._p.command

    def done(self) -> bool:
        return self._p.done()

    def wait(self, timeout: Optional[float] = None) -> Any:
        return self._conn._wait(self._p, timeout)


Listener = Callable[[str, Any], None]


class Connection:
    """One client connection to a ctsim server."""

    #: outbound bytes allowed to wait for a server that does not read
    MAX_QUEUED = 64 << 20

    def __init__(self, stream, name: str = "ctsim", max_body: int = MAX_BODY):
        self._stream = stream
        self.name = name
        self._parser = FrameParser(max_body)
        self._lock = threading.Lock()
        self._seq = 0
        self._pending: Dict[int, _Pending] = {}
        self._listeners: List[Listener] = []
        self._close_listeners: List[Callable[[Optional[str]], None]] = []
        self._closed = threading.Event()
        self._closing_by_us = False
        #: the server is held by a debugger in page code (its ctsim/debugPaused): it reads
        #: and answers nothing else until it goes on, so a write that waits and a request
        #: that times out meanwhile do not end the connection
        self.held = False
        self.close_reason: Optional[str] = None
        self._reader: Optional[threading.Thread] = None
        self._writer: Optional[threading.Thread] = None
        self._out: "collections.deque[bytes]" = collections.deque()
        self._out_bytes = 0
        self._out_ready = threading.Event()
        self._out_idle = threading.Event()
        self._out_idle.set()
        self.messages_in = 0

    # -- life ---------------------------------------------------------------------------
    def start(self) -> "Connection":
        gcguard.before_worker()
        self._writer = threading.Thread(target=self._write_loop, name="ctsim-write " + self.name, daemon=True)
        self._writer.start()
        t = threading.Thread(target=self._read_loop, name="ctsim-read " + self.name, daemon=True)
        self._reader = t
        t.start()
        return self

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def on_reader_thread(self) -> bool:
        """The caller runs on this connection's reader thread (a listener or a callback):
        a response cannot arrive while it waits, so it must not wait for one."""
        return self._reader is not None and self._reader is threading.current_thread()

    def close(self, drain: float = 1.0) -> None:
        """Close the connection (pending requests fail; the server keeps running). Frames
        still queued get ``drain`` seconds to go out first."""
        self._closing_by_us = True
        if self._writer is not None and self._writer is not threading.current_thread():
            self._out_idle.wait(drain)
        try:
            self._stream.close()
        except Exception:  # noqa: BLE001 -- closing never fails its caller
            pass
        self._lost(None)
        t = self._reader
        if t is not None and t is not threading.current_thread():
            t.join(2.0)

    def add_listener(self, fn: Listener) -> None:
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def on_close(self, fn: Callable[[Optional[str]], None]) -> None:
        """``fn(reason)`` when the connection ends (at once when it already has); reason None
        = closed by this client."""
        with self._lock:
            if not self._closed.is_set():
                self._close_listeners.append(fn)
                return
        self._call_close(fn, self.close_reason)

    def remove_close_listener(self, fn) -> None:
        """Forget an on_close listener (a user that let go of a shared connection: its
        closure would otherwise keep the user alive as long as the connection)."""
        with self._lock:
            if fn in self._close_listeners:
                self._close_listeners.remove(fn)

    # -- requests -----------------------------------------------------------------------
    def _send(self, cmd: str, arguments: Any, timeout: Optional[float]) -> _Pending:
        cmd = command(cmd)
        with self._lock:
            if self._closed.is_set():
                raise ConnectionLost(self._lost_text())
            self._seq += 1
            p = _Pending(self._seq, cmd, timeout)
            self._pending[p.seq] = p
            msg: Dict[str, Any] = {"seq": p.seq, "type": "request", "command": cmd}
            if arguments is not None:
                msg["arguments"] = arguments
            try:
                frame = encode(msg)
            except (TypeError, ValueError) as e:
                self._pending.pop(p.seq, None)
                raise CtsimError("A request could not be sent: %s." % e)
            over = not self._enqueue_locked(frame)
        if over:
            self._lost("The Cicode simulator does not read its messages.")
            raise ConnectionLost(self._lost_text())
        return p

    def _enqueue_locked(self, frame: bytes) -> bool:
        """Queue a frame (the caller holds the lock); False when the queue is full."""
        self._out.append(frame)
        self._out_bytes += len(frame)
        self._out_idle.clear()
        self._out_ready.set()
        return self._out_bytes <= self.MAX_QUEUED

    def _write_loop(self) -> None:
        while True:
            self._out_ready.wait(_POLL_MS / 1000.0)
            with self._lock:
                frames = list(self._out)
                self._out.clear()
                self._out_ready.clear()
                done = self._closed.is_set()
            for f in frames:
                try:
                    try:
                        self._stream.write(f, hold=lambda: self.held)
                    except TypeError:
                        self._stream.write(f)  # (a stream without the hold: tests)
                except Exception as e:  # noqa: BLE001 -- a write error ends the connection
                    self._out_idle.set()
                    self._lost(None if self._closing_by_us else str(e))
                    return
                with self._lock:
                    self._out_bytes -= len(f)
            with self._lock:
                if not self._out:
                    self._out_idle.set()
            if done:
                self._out_idle.set()
                return

    def request(self, cmd: str, arguments: Any = None, timeout: Optional[float] = DEFAULT_TIMEOUT) -> Any:
        """Send a request and wait for its response's body (``{}`` when it has none)."""
        p = self._send(cmd, arguments, timeout)
        return self._wait(p, timeout)

    def request_async(
        self,
        cmd: str,
        arguments: Any = None,
        callback: Optional[Callable[[Any, Optional[CtsimError]], None]] = None,
        timeout: Optional[float] = DEFAULT_TIMEOUT,
    ) -> PendingRequest:
        """Send without waiting. ``callback(body, error)`` runs on the reader thread when the
        response comes (or the timeout passes, or the connection ends)."""
        p = self._send(cmd, arguments, timeout)
        if callback is not None:
            fire = False
            with self._lock:
                p.callback = callback
                fire = p.done()
            if fire:
                self._fire(p)
        return PendingRequest(self, p)

    def _wait(self, p: _Pending, timeout: Optional[float]) -> Any:
        if not p.event.is_set() and self.on_reader_thread():
            # the answer would be read by this very thread: waiting only times out (and
            # stalls every other request meanwhile). A listener uses request_async.
            with self._lock:
                self._pending.pop(p.seq, None)
            raise CtsimError(
                "A Cicode simulator request cannot wait for its answer on the "
                "thread that reads the answers (use request_async)."
            )
        if not p.event.wait(timeout):
            with self._lock:
                self._pending.pop(p.seq, None)
                if not p.event.is_set():
                    p.error = RequestTimeout(p.command, timeout or 0)
                    p.event.set()
        if p.error is not None:
            raise p.error
        return p.body if p.body is not None else {}

    def _finish(self, p: _Pending, body: Any = None, error: Optional[CtsimError] = None) -> None:
        with self._lock:
            if p.done():
                return
            p.body = body
            p.error = error
            p.event.set()
            fire = p.callback is not None
        if fire:
            self._fire(p)

    def _fire(self, p: _Pending) -> None:
        with self._lock:
            cb, p.callback = p.callback, None
        if cb is None:
            return
        try:
            cb(p.body if p.error is None else None, p.error)
        except Exception:  # noqa: BLE001 -- a callback bug must not end the reader
            logsetup.note_exception("sim.client.callback", command=p.command)

    # -- reading ------------------------------------------------------------------------
    def _read_loop(self) -> None:
        reason: Optional[str] = None
        try:
            while not self._closed.is_set():
                data = self._stream.read(timeout=_POLL_MS / 1000.0)
                if data is None:
                    self._expire()
                    continue
                if not data:
                    if not self._parser.at_frame_start():
                        reason = "The Cicode simulator ended inside a message."
                    else:
                        reason = "The Cicode simulator closed the connection."
                    break
                for body in self._parser.feed(data):
                    self._handle(body)
                self._expire()
        except FramingError as e:
            reason = "The Cicode simulator sent a broken message (%s)." % e
        except Exception as e:  # noqa: BLE001 -- the reader never dies silently
            reason = "Reading from the Cicode simulator failed (%s)." % e
            logsetup.note_exception("sim.client.read")
        # the reason first (the writer's error on the closed stream must not replace it)
        self._lost(None if self._closing_by_us else reason)
        try:
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass

    def _restart_deadlines(self) -> None:
        """The hold ended: the requests sent meanwhile get their whole timeout again."""
        now = time.monotonic()
        with self._lock:
            for p in self._pending.values():
                if p.deadline is not None and p.timeout:
                    p.deadline = max(p.deadline, now + p.timeout)

    def _expire(self) -> None:
        if self.held:
            return  # (nothing is answered while held)
        now = time.monotonic()
        late = []
        with self._lock:
            for seq, p in list(self._pending.items()):
                if p.deadline is not None and now >= p.deadline:
                    self._pending.pop(seq, None)
                    late.append(p)
        for p in late:
            self._finish(p, error=RequestTimeout(p.command, p.timeout or 0))

    def _handle(self, raw: bytes) -> None:
        self.messages_in += 1
        try:
            msg = json.loads(raw.decode("utf-8", "replace") or "null")
        except ValueError:
            log.warning("ctsim: a message that is not JSON was ignored")
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "response":
            try:
                seq = int(msg.get("request_seq"))
            except TypeError, ValueError:
                return
            with self._lock:
                p = self._pending.pop(seq, None)
            if p is None:
                return  # timed out already, or not ours
            if msg.get("success", True):
                self._finish(p, body=msg.get("body") if msg.get("body") is not None else {})
            else:
                text = _failure_text(msg)
                body = msg.get("body")
                cls = NotAvailable if _is_unknown_request(msg.get("message") or "", body) else RequestFailed
                self._finish(p, error=cls(p.command, text, body))
        elif kind == "event":
            name = str(msg.get("event") or "")
            body = msg.get("body")
            if name == "ctsim/debugPaused" and isinstance(body, dict):
                self.held = bool(body.get("paused"))
                if not self.held:
                    self._restart_deadlines()
            with self._lock:
                listeners = list(self._listeners)
            for fn in listeners:
                try:
                    fn(name, body if body is not None else {})
                except Exception:  # noqa: BLE001 -- a listener bug must not end the reader
                    logsetup.note_exception("sim.client.listener", event=name)
        elif kind == "request":
            # a reverse request (runInTerminal ...): a viewer does none of them
            with self._lock:
                self._seq += 1
                self._enqueue_locked(
                    encode(
                        {
                            "seq": self._seq,
                            "type": "response",
                            "request_seq": msg.get("seq", 0),
                            "success": False,
                            "command": msg.get("command", ""),
                            "message": "not supported by ctgedit",
                        }
                    )
                )

    def _lost(self, reason: Optional[str]) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self.close_reason = reason
            self._closed.set()
            pend = list(self._pending.values())
            self._pending.clear()
            closers = list(self._close_listeners)
            self._close_listeners.clear()
        err = ConnectionLost(self._lost_text())
        for p in pend:
            self._finish(p, error=err)
        for fn in closers:
            self._call_close(fn, reason)

    def _lost_text(self) -> str:
        return self.close_reason or "The connection to the Cicode simulator is closed."

    @staticmethod
    def _call_close(fn, reason) -> None:
        try:
            fn(reason)
        except Exception:  # noqa: BLE001
            logsetup.note_exception("sim.client.on_close")


def _failure_text(msg: dict) -> str:
    text = msg.get("message") or ""
    body = msg.get("body")
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            fmt = err.get("format") or ""
            if fmt:
                text = fmt if not text or text in ("error", "failed") else text
        elif isinstance(err, str) and not text:
            text = err
    return str(text)


# -- the engine on disk -----------------------------------------------------------------------
@dataclasses.dataclass
class EngineLocation:
    """Where ctsim.exe is. ``argv`` = the command line that runs it (tests run a Python fake
    of the server this way); default ``[exe]``."""

    exe: str
    origin: str = ""  # 'setting', 'environment', 'bundled', 'checkout'
    argv: Optional[List[str]] = None

    def command(self, *args: str) -> List[str]:
        return list(self.argv or [self.exe]) + [str(a) for a in args]

    @property
    def folder(self) -> str:
        return os.path.dirname(self.exe)

    @property
    def dll(self) -> str:
        return os.path.join(self.folder, DLL_NAME)


def repo_root() -> str:
    """The checkout this module runs from (the folder holding the ctgedit package)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def checkout_build_dir(root: Optional[str] = None) -> str:
    """The engine checkout's net48 Release folder (where a development build puts ctsim)."""
    return os.path.join(root or repo_root(), *CHECKOUT_BUILD)


def _complete(path: str) -> str:
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(path).strip().strip('"'))))
    return os.path.join(path, EXE_NAME) if os.path.isdir(path) else path


def _usable(exe: str) -> bool:
    return os.path.isfile(exe) and os.path.isfile(os.path.join(os.path.dirname(exe), DLL_NAME))


def engine_candidates(setting: Optional[str] = None) -> List[Tuple[str, str]]:
    """(path, origin) in the order find_engine tries them."""
    out: List[Tuple[str, str]] = []
    if setting:
        out.append((_complete(setting), "setting"))
    env = os.environ.get(PATH_ENV, "").strip()
    if env:
        out.append((_complete(env), "environment"))
    if getattr(sys, "frozen", False):
        here = os.path.dirname(os.path.abspath(sys.executable))
        out.append((os.path.join(here, EXE_NAME), "bundled"))
        mei = getattr(sys, "_MEIPASS", "")
        if mei:
            out.append((os.path.join(mei, EXE_NAME), "bundled"))
    out.append((os.path.join(checkout_build_dir(), EXE_NAME), "checkout"))
    return out


def find_engine(setting: Optional[str] = None) -> EngineLocation:
    """ctsim.exe with CicodeSim.Engine.dll beside it. A path set in ``setting`` that is not
    there is an error of its own (it is not passed over for another copy)."""
    searched = []
    for path, origin in engine_candidates(setting):
        if _usable(path):
            return EngineLocation(path, origin)
        searched.append(path)
        if origin == "setting":
            if os.path.isfile(path):
                raise EngineNotFound(
                    "%s is not next to the Cicode simulator chosen (%s)." % (DLL_NAME, path), searched
                )
            raise EngineNotFound("The Cicode simulator chosen is not there any more: %s." % path, searched)
    raise EngineNotFound("The Cicode simulator (ctsim.exe) was not found.", searched)


def engine_version(loc: EngineLocation, timeout: float = 10.0) -> str:
    """``ctsim --version`` ('' when it cannot be run)."""
    try:
        r = subprocess.run(
            loc.command("--version"),
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            stdin=subprocess.DEVNULL,
        )
    except OSError, subprocess.SubprocessError:
        return ""
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else ""


# -- discovery ---------------------------------------------------------------------------------
#: the discovery file format (Host/Discovery.cs ServerEntry.Format 'ctsim-server/0')
DISCOVERY_FORMAT = "ctsim-server/"


def discovery_dir() -> str:
    env = os.environ.get(SERVERS_ENV, "").strip()
    if env:
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "cicode-sim", "servers")


def project_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path or "")))).rstrip("\\/")


def root_key(root: Optional[str]) -> str:
    """A root (a compiled project's name) compared without case; '' = no root asked."""
    return str(root or "").strip().lower()


def _base_name(path: str) -> str:
    return os.path.basename(str(path or "").rstrip("\\/"))


@dataclasses.dataclass
class ServerEntry:
    """One discovery file of a running server."""

    path: str
    id: str = ""
    pid: int = 0
    started: Any = None
    project: str = ""
    user: str = ""
    install: str = ""
    transport: str = "pipe"
    address: str = ""
    version: str = ""
    mode: str = ""
    raw: Dict[str, Any] = dataclasses.field(default_factory=dict)
    #: the compiled project it runs as
    root: str = ""

    def same_project(self, project: str) -> bool:
        return bool(self.project) and project_key(self.project) == project_key(project)

    def runs_as(self) -> str:
        """The root it runs as (an entry without one: the project itself)."""
        return self.root or _base_name(self.project)

    def same_root(self, root: Optional[str]) -> bool:
        """It runs as ``root`` (None / '' = any root)."""
        return not root_key(root) or root_key(self.runs_as()) == root_key(root)


_FILETIME_UNIX = 116444736000000000  # 1970-01-01 as a FILETIME
_TICKS_FILETIME = 504911232000000000  # 1601-01-01 in .NET ticks


def filetime_of(value: Any) -> Optional[int]:
    """A process start time as a Windows FILETIME (100 ns since 1601, UTC), from what a
    discovery file may hold: a FILETIME, .NET ticks, Unix seconds or milliseconds, an ISO
    8601 text (with or without its zone: none = local time). None when it cannot be told."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        t = value.strip()
        if re.fullmatch(r"[+-]?\d+(\.\d+)?", t):
            value = float(t) if "." in t else int(t)
        else:
            if t.endswith("Z"):
                t = t[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(t)
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.astimezone()
            secs = dt.timestamp()
            return int(round(secs * 1e7)) + _FILETIME_UNIX
    if isinstance(value, (int, float)):
        v = value  # an int stays exact (no float round)
        if v > 5e17:
            return int(v) - _TICKS_FILETIME
        if v > 1e16:
            return int(v)
        if v > 1e11:
            return int(round(v * 1e4)) + _FILETIME_UNIX  # Unix milliseconds
        if v > 1e8:
            return int(round(v * 1e7)) + _FILETIME_UNIX  # Unix seconds
    return None


#: a start time in a discovery file matches the process within this (FILETIME units: 2 s;
#: a text time can be rounded to the second)
START_TOLERANCE = 2 * 10_000_000


def entry_alive(e: ServerEntry) -> bool:
    """The server process of the entry still runs (the same process, not a later one that
    got its pid)."""
    if e.pid <= 0 or not paths.pid_alive(e.pid):
        return False
    want = filetime_of(e.started)
    if want is None:
        return True
    have = paths.process_started(e.pid)
    return not have or abs(have - want) <= START_TOLERANCE


def _entry_from(path: str, data: dict) -> Optional[ServerEntry]:
    def first(*keys, default=""):
        for k in keys:
            v = data.get(k)
            if v not in (None, ""):
                return v
        return default

    try:
        pid = int(first("pid", "processId", default=0))
    except TypeError, ValueError:
        pid = 0
    transport = str(first("transport", default="")).lower()
    address = str(first("address", default=""))
    pipe = str(first("pipe", "pipeName", default=""))
    if pipe and (not transport or transport == "pipe"):
        transport, address = "pipe", pipe
    if not transport:
        transport = "pipe"
    if transport == "tcp" and not address:
        port = first("port", default="")
        if port:
            address = "%s:%s" % (first("host", default="127.0.0.1"), port)
    if not address:
        return None
    sid = str(first("id", default=os.path.splitext(os.path.basename(path))[0]))
    return ServerEntry(
        path=path,
        id=sid,
        pid=pid,
        # the PROCESS start ('started' is when the server began serving,
        # seconds later: not a process identity)
        started=first("processStart", "processStartIso", "processStarted", "startTime", default=None),
        project=str(first("project", "projectDir", "projectPath", default="")),
        user=str(first("user", "userDir", "userFolder", default="")),
        install=str(first("install", "installDir", default="")),
        transport=transport,
        address=address,
        version=str(first("version", default="")),
        mode=str(first("mode", default="")),
        raw=data,
        root=str(first("root", default="")),
    )


def read_servers(folder: Optional[str] = None, alive_only: bool = True) -> List[ServerEntry]:
    """The discovery entries in ``folder`` (default discovery_dir()), newest first; with
    ``alive_only`` the ones whose process is gone are left out (their files are left
    alone: each server removes its own)."""
    folder = folder or discovery_dir()
    out: List[Tuple[float, ServerEntry]] = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, "rb") as fh:
                data = json.loads(fh.read(1 << 20).decode("utf-8-sig", "replace"))
            mtime = os.path.getmtime(path)
        except OSError, ValueError:
            continue
        if not isinstance(data, dict):
            continue
        fmt = data.get("format")
        if fmt is not None and not str(fmt).startswith(DISCOVERY_FORMAT):
            continue  # another program's file, or a format this is not
        e = _entry_from(path, data)
        if e is None or (alive_only and not entry_alive(e)):
            continue
        out.append((mtime, e))
    out.sort(key=lambda m: -m[0])
    return [e for _m, e in out]


def servers_for(project: str, folder: Optional[str] = None, root: Optional[str] = None) -> List[ServerEntry]:
    """The running servers of ``project`` (run as ``root`` when given), newest first."""
    return [e for e in read_servers(folder) if e.same_project(project) and e.same_root(root)]


# -- a server this process starts -------------------------------------------------------------
from ..infra.workers import KillOnClose as _Job  # noqa: E402 -- (the shared job object)


_PIPE_IN_OUTPUT = re.compile(r"(\\\\\.\\pipe\\ctsim\.[^\s\"']+)", re.I)

#: pids of servers this process started and is stopping: never attached to (a session opened
#: while one ends would join a server that is going away)
_STOPPING: set = set()
_STOPPING_LOCK = threading.Lock()


def _stopping_add(pid: int) -> None:
    with _STOPPING_LOCK:
        _STOPPING.add(int(pid))


def _stopping_discard(pid: int) -> None:
    with _STOPPING_LOCK:
        _STOPPING.discard(int(pid))


def stopping(pid: int) -> bool:
    """A server this process started is being stopped (its discovery entry still shows)."""
    with _STOPPING_LOCK:
        return int(pid) in _STOPPING


def server_environment() -> Dict[str, str]:
    """The environment for a ctsim process this client starts."""
    env = dict(os.environ)
    from ..infra import install

    if install.is_overridden():
        env.setdefault("CICODESIM_INI", install.active_ini() or "")
        b = install.bin_dir()
        if b:
            env.setdefault("CICODESIM_SCADA_BIN", b)
        f0 = install.func0_dbf()
        if f0:
            env.setdefault("CICODESIM_FUNC0", f0)
    return env


class StartedServer:
    """A ``ctsim serve`` process this client started: its output is drained on a thread
    (the last lines kept for messages); ``stop`` ends it."""

    def __init__(self, loc: EngineLocation, project: str, proc: subprocess.Popen, root: Optional[str] = None):
        self.location = loc
        self.project = project
        self.root = root or ""
        self.proc = proc
        self.output: "collections.deque[str]" = collections.deque(maxlen=OUTPUT_LINES)
        self.announced = ""  # a pipe the server printed (plain text)
        self.ready: Optional[Dict[str, Any]] = None  # its ready line (JSON)
        self.discovery_path = ""  # its discovery file, once known
        self._drain = threading.Thread(
            target=self._read_output, daemon=True, name="ctsim-output %d" % proc.pid
        )
        self._drain.start()

    @property
    def pid(self) -> int:
        return self.proc.pid

    @classmethod
    def start(cls, loc: EngineLocation, project: str, root: Optional[str] = None) -> "StartedServer":
        gcguard.before_worker()
        argv = loc.command("serve", "--project", project)
        if root:
            argv += ["--root", root]
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=loc.folder or None,
                env=server_environment(),
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as e:
            raise ServerUnavailable("The Cicode simulator could not be started (%s)." % e)
        _Job.assign(proc)
        log.info("ctsim: started %s (pid %d) for %s", " ".join(argv), proc.pid, project)
        return cls(loc, project, proc, root)

    @staticmethod
    def decode_line(raw: bytes) -> str:
        """One line of the server's output: its ready line is UTF-8, but the .NET console
        writes stderr (the error a failed start prints) in the OEM code page when the process
        has no console."""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        try:
            return raw.decode("oem")  # Windows only (the console code page)
        except LookupError, UnicodeDecodeError:
            return raw.decode("utf-8", "replace")

    def _read_output(self) -> None:
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                line = self.decode_line(raw).rstrip("\r\n")
                self.output.append(line)
                if self.ready is None and line.lstrip().startswith("{"):
                    # 'ctsim serve' prints one ready line: {"event": "ready", "id", "pipe",
                    # "transport", "address", "pid", "discovery", "project", "root", ...}
                    try:
                        data = json.loads(line)
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and data.get("event") == "ready":
                        self.ready = data
                        continue
                if not self.announced:
                    m = _PIPE_IN_OUTPUT.search(line)
                    if m:
                        self.announced = m.group(1)
        except OSError, ValueError:
            pass

    def ready_entry(self) -> Optional[ServerEntry]:
        """The server's ready line as a discovery entry (None before it came)."""
        data = self.ready
        if data is None:
            return None
        e = _entry_from(
            str(data.get("discovery") or ""),
            dict(data, project=data.get("project") or self.project, pid=data.get("pid") or self.pid),
        )
        return e

    def running(self) -> bool:
        return self.proc.poll() is None

    def tail(self, n: int = 6) -> str:
        lines = [x for x in list(self.output) if x.strip()]
        return "\n".join(lines[-n:])

    def wait_announced(self, folder: Optional[str], timeout: float) -> ServerEntry:
        """Where the server listens: its ready line, the discovery entry it wrote (its pid),
        or a pipe it printed."""
        deadline = time.monotonic() + timeout
        while True:
            e = self.ready_entry()
            if e is not None:
                self.discovery_path = e.path
                return e
            for e in read_servers(folder, alive_only=False):
                if e.pid == self.pid:
                    self.discovery_path = e.path
                    return e
            if self.announced:
                return ServerEntry(
                    path="",
                    id=self.announced.rsplit(".", 1)[-1],
                    pid=self.pid,
                    project=self.project,
                    transport="pipe",
                    address=self.announced,
                    root=self.root,
                )
            code = self.proc.poll()
            if code is not None:
                time.sleep(0.05)  # let the drain thread take the last lines
                raise ServerUnavailable(self._exit_text(code))
            if time.monotonic() >= deadline:
                self.stop()
                raise ServerUnavailable(
                    "The Cicode simulator did not start its server within %g s." % timeout
                )
            time.sleep(0.1)

    def _exit_text(self, code: int) -> str:
        out = self.tail(60)
        out = self.tail()
        # the engine says why on one line: 'ctsim serve: no compiled code: <dir>\_LIBRARY.RDB
        # does not exist (compile the project ...)', 'ctsim: cannot listen: ...'
        why = ""
        for line in reversed([x.strip() for x in self.output if x.strip()]):
            m = re.match(r"ctsim(?: serve| dap)?:\s*(.+)$", line)
            if m:
                why = m.group(1).rstrip(".")
                break
        if "no compiled code" in why.lower():
            # (the common case, said as the next step; the engine's own line with the path
            # and the exit code is the detail below)
            text = "The project is not compiled: compile it in Plant SCADA Studio, then click Reconnect."
            out = "The Cicode simulator did not start: %s (exit code %d).\n%s" % (why, code, out)
        elif why:
            text = "The Cicode simulator did not start: %s." % why
            out = "Exit code %d.\n%s" % (code, out)
        else:
            text = "The Cicode simulator ended at its start (exit code %d)." % code
        return text + ("\n" + out.rstrip() if out.strip() else "")

    def stop(self, timeout: float = 3.0, grace: float = 0.5) -> None:
        """End the process: ``grace`` seconds to go by itself (its last client left), then
        TerminateProcess, waiting up to ``timeout``. A server killed that way never removes
        its own discovery file (the engine does that on a normal end): it is removed here."""
        _stopping_add(self.pid)
        if self.proc.poll() is None and grace > 0:
            try:
                self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                pass
        forced = False
        if self.proc.poll() is None:
            forced = True
            try:
                self.proc.terminate()
            except OSError:
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.kill()
                except OSError:
                    pass
        try:
            if self.proc.stdout is not None:
                self.proc.stdout.close()
        except OSError, ValueError:
            pass
        ended = self.proc.poll() is not None
        if forced and ended:
            self._remove_own_discovery()
        if ended:
            _stopping_discard(self.pid)
        log.info("ctsim: stopped the server pid %d (exit code %s)", self.pid, self.proc.returncode)

    def _remove_own_discovery(self) -> None:
        path = self.discovery_path or str((self.ready or {}).get("discovery") or "")
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                data = json.loads(fh.read(1 << 20).decode("utf-8-sig", "replace"))
            if isinstance(data, dict) and int(data.get("pid") or 0) == self.pid:
                os.remove(path)
        except OSError, ValueError, TypeError:
            pass


# -- a session: one server, one project ----------------------------------------------------------
@dataclasses.dataclass
class ClockInfo:
    running: bool = False
    speed: float = 1.0
    scan: int = 0
    time: float = 0.0  # simulated seconds
    known: bool = False  # False = no reply carried the clock yet


def clock_from(body: Any, base: Optional[ClockInfo] = None) -> ClockInfo:
    c = dataclasses.replace(base) if base is not None else ClockInfo()
    if not isinstance(body, dict):
        return c
    if isinstance(body.get("clock"), dict):
        body = body["clock"]
    got = False
    if "running" in body:
        c.running, got = bool(body["running"]), True
    elif body.get("mode") in ("run", "pause", "paused", "step"):
        c.running, got = body["mode"] == "run", True
    # the engine's ClockState: {mode run | paused, speed, ms, t (seconds), time (ISO wall
    # time), start, stepSize}; 'time' is a number only in other shapes
    for key, attr, conv in (
        ("speed", "speed", float),
        ("scan", "scan", int),
        ("t", "time", float),
        ("simTime", "time", float),
        ("seconds", "time", float),
        ("time", "time", float),
    ):
        if key in body and not (key == "time" and ("t" in body or isinstance(body[key], str))):
            try:
                setattr(c, attr, conv(body[key]))
                got = True
            except TypeError, ValueError:
                pass
    if "t" not in body and "ms" in body:
        try:
            c.time, got = float(body["ms"]) / 1000.0, True
        except TypeError, ValueError:
            pass
    if "scan" not in body and got:
        c.scan = int(c.time / SCAN_SECONDS + 1e-9)
    c.known = c.known or got
    return c


def _camel_to_snake(key: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", lambda m: "_" + m.group(1).lower(), key).lower()


def quality_text(q: Any) -> str:
    """'Good' / 'Uncertain' / 'Bad' from a text or an OPC-style quality number."""
    if q is None or q == "":
        return "Good"
    if isinstance(q, bool):
        return "Good" if q else "Bad"
    if isinstance(q, (int, float)):
        n = int(q)
        return "Good" if n >= 192 else ("Uncertain" if n >= 64 else "Bad")
    t = str(q).strip().lower()
    if t.startswith("good"):
        return "Good"
    if t.startswith("uncertain"):
        return "Uncertain"
    if t.startswith("bad"):
        return "Bad"
    return str(q)


def normalise_tag(d: Any, equipment: str = "") -> Optional[Dict[str, Any]]:
    """One tag of a ctsim/tags reply as plain keys (name, type, value, quality, eng_zero,
    eng_full, units, equipment, item, comment, cluster, project, page_reads, timestamp)."""
    if not isinstance(d, dict):
        return None
    t = {_camel_to_snake(k): v for k, v in d.items()}
    name = t.get("name") or t.get("tag")
    if not name:
        return None

    def num(*keys):
        for k in keys:
            v = t.get(k)
            if v in (None, ""):
                continue
            try:
                f = float(v)
            except TypeError, ValueError:
                continue
            if math.isfinite(f):  # a range is never INF / NaN (the panel divides)
                return f
        return None

    eq = t.get("equipment") or t.get("equip") or equipment or ""
    return {
        "name": str(name),
        "type": str(t.get("type") or t.get("data_type") or "REAL"),
        "value": t.get("value"),
        "quality": quality_text(t.get("quality")),
        "eng_zero": num("eng_zero", "eng_min", "min"),
        "eng_full": num("eng_full", "eng_max", "max"),
        "units": str(t.get("units") or t.get("eng_units") or ""),
        "equipment": str(eq),
        "item": str(t.get("item") or ""),
        "comment": str(t.get("comment") or t.get("description") or ""),
        "cluster": str(t.get("cluster") or ""),
        "project": str(t.get("project") or ""),
        "page_reads": bool(t.get("page_reads") or t.get("on_page") or t.get("read")),
        "timestamp": t.get("timestamp"),
        # a name the project does not define (engine/PROTOCOL.md ctsim/tags {page}):
        # 'undefined', or 'simulated' once the simulator made a tag for it
        "defined": t.get("defined") is not False,
        "simulated": bool(t.get("simulated")) or t.get("kind") == "simulated",
        "type_from": str(t.get("type_from") or ""),
        # a tag a '?name?' reference points at through the window's associations
        "via": str(t.get("via") or ""),
        "ref": str(t.get("ref") or ""),
        # a simulator-only lock (ctsim/lockTag): Cicode cannot change the value
        "locked": bool(t.get("locked")),
        # the definition fields a simulation can override (ctsim/setTagProperty): the project's, else the override; 'overrides' / 'configured' =
        # {FIELD: text} of the overridden ones (the override, the project's)
        "format": str(t.get("format") or ""),
        "raw_zero": num("raw_zero"),
        "raw_full": num("raw_full"),
        "deadband": num("deadband"),
        "io_device": str(t.get("unit") or ""),
        "address": str(t.get("address") or ""),
        "custom": tuple(str(t.get("custom%d" % k) or "") for k in range(1, 9)),
        "overrides": dict(t.get("overrides") or {}) if isinstance(t.get("overrides"), dict) else {},
        "configured": dict(t.get("configured") or {}) if isinstance(t.get("configured"), dict) else {},
    }


@dataclasses.dataclass
class TagPage:
    tags: List[Dict[str, Any]]
    total: int
    start: int = 0


def windows_from(body: Any) -> List[Dict[str, Any]]:
    """A windowsChanged body / windows reply as a list of window dicts (each with an int
    'window', 'page', 'parent' (-1 = none), 'path' and 'hasModel'); entries without a usable
    window number are left out."""
    out: List[Dict[str, Any]] = []
    items = body.get("windows") if isinstance(body, dict) else body
    for w in items or ():
        if not isinstance(w, dict):
            continue
        try:
            n = int(w.get("window"))
        except TypeError, ValueError:
            continue
        d = dict(w)
        d["window"] = n
        try:
            d["parent"] = int(w.get("parent", -1))
        except TypeError, ValueError:
            d["parent"] = -1
        d["page"] = str(w.get("page") or "")
        d["path"] = str(w.get("path") or "")
        d["hasModel"] = bool(w.get("hasModel"))
        out.append(d)
    return out


def tag_page_from(body: Any, start: int = 0) -> TagPage:
    """A ctsim/tags reply: ``{tags: [...], total, start}``, or the tags grouped by equipment
    (``{equipment: [{name, tags: [...]}]}`` / ``{groups: ...}``)."""
    body = body if isinstance(body, dict) else {}
    rows: List[Dict[str, Any]] = []
    for t in body.get("tags") or ():
        n = normalise_tag(t)
        if n is not None:
            rows.append(n)
    for key in ("equipment", "groups"):
        groups = body.get(key)
        if isinstance(groups, list):
            for g in groups:
                if not isinstance(g, dict):
                    continue
                for t in g.get("tags") or ():
                    n = normalise_tag(t, str(g.get("name") or g.get("equipment") or ""))
                    if n is not None:
                        rows.append(n)
    total = body.get("total", body.get("count"))
    try:
        total = int(total)
    except TypeError, ValueError:
        total = start + len(rows)
    try:
        start = int(body.get("start", start))
    except TypeError, ValueError:
        pass
    return TagPage(rows, max(total, start + len(rows)), start)


def written_tag(body: Any, name: str = "") -> Dict[str, Any]:
    """A setTag reply's tag after the write: ``{error, tag: {name, value, quality}}`` (also a
    flat ``{value, quality}``)."""
    body = body if isinstance(body, dict) else {}
    t = body.get("tag") if isinstance(body.get("tag"), dict) else body
    out = {"name": str(t.get("name") or name), "value": t.get("value")}
    if "quality" in t:
        out["quality"] = quality_text(t.get("quality"))
    return out


def tag_changes_from(body: Any) -> List[Dict[str, Any]]:
    """The values of a tagsChanged event: ``{tags: [{name, value, quality?}]}`` (also
    ``changes`` / ``values``, or a ``{name: value}`` object)."""
    body = body if isinstance(body, dict) else {}
    items = body.get("tags", body.get("changes", body.get("values")))
    out: List[Dict[str, Any]] = []
    if isinstance(items, dict):
        items = [{"name": k, "value": v} for k, v in items.items()]
    for t in items or ():
        if isinstance(t, dict) and (t.get("name") or t.get("tag")):
            out.append(
                {
                    "name": str(t.get("name") or t.get("tag")),
                    "value": t.get("value"),
                    "quality": quality_text(t.get("quality")) if "quality" in t else None,
                    "timestamp": t.get("timestamp"),
                }
            )
    return out


# -- alarms and equipment (ctsim/alarms, ctsim/equipment) ---------------
@dataclasses.dataclass
class AlarmList:
    alarms: List[Dict[str, Any]]
    total: int
    start: int = 0
    serial: Optional[int] = None


@dataclasses.dataclass
class EquipmentList:
    equipment: List[Dict[str, Any]]
    total: int
    start: int = 0


def _state_word(a: Dict[str, Any]) -> str:
    """An alarm's state word from its flags when the server sent none."""
    if a.get("disabled"):
        return "disabled"
    on, ack = bool(a.get("on")), bool(a.get("ack"))
    if on:
        return "ack_on" if ack else "unack_on"
    return "off" if ack or not a.get("onTime") else "unack_off"


def normalise_alarm(d: Any) -> Optional[Dict[str, Any]]:
    """One alarm of a ctsim/alarms reply: tag, name, desc, equipment, item, type, category,
    priority, state ('off' | 'unack_on' | 'ack_on' | 'unack_off' | 'disabled'), on, ack,
    disabled, onTime ('' when none)."""
    if not isinstance(d, dict):
        return None
    tag = str(d.get("tag") or d.get("name") or "")
    if not tag:
        return None
    a = {
        "tag": tag,
        "name": str(d.get("name") or ""),
        "desc": str(d.get("desc") or ""),
        "equipment": str(d.get("equipment") or ""),
        "item": str(d.get("item") or ""),
        "type": str(d.get("type") or ""),
        "category": d.get("category"),
        "priority": d.get("priority"),
        "on": bool(d.get("on")),
        "ack": bool(d.get("ack")),
        "disabled": bool(d.get("disabled")),
        "onTime": str(d.get("onTime") or ""),
    }
    state = str(d.get("state") or "").lower()
    a["state"] = state if state in ("off", "unack_on", "ack_on", "unack_off", "disabled") else _state_word(a)
    return a


def alarm_list_from(body: Any) -> AlarmList:
    body = body if isinstance(body, dict) else {}
    rows = [a for a in (normalise_alarm(x) for x in body.get("alarms") or ()) if a is not None]
    try:
        total = int(body.get("total"))
    except TypeError, ValueError:
        total = len(rows)
    try:
        start = int(body.get("start") or 0)
    except TypeError, ValueError:
        start = 0
    serial = body.get("serial")
    return AlarmList(rows, max(total, start + len(rows)), start, serial if isinstance(serial, int) else None)


#: the runtime fields of an equipment ctsim/equipment lists and setEquipment sets
EQUIPMENT_FIELDS = ("MODE", "STATE", "DRMODE")


def equipment_list_from(body: Any) -> EquipmentList:
    body = body if isinstance(body, dict) else {}
    rows = []
    for d in body.get("equipment") or ():
        if not isinstance(d, dict) or not d.get("name"):
            continue
        up = {str(k).upper(): v for k, v in d.items()}
        e = {"name": str(d["name"]), "type": str(d.get("type") or ""), "scheduled": bool(d.get("scheduled"))}
        for f in EQUIPMENT_FIELDS:
            v = up.get(f)
            e[f] = "" if v is None else str(v)
        rows.append(e)
    try:
        total = int(body.get("total"))
    except TypeError, ValueError:
        total = len(rows)
    try:
        start = int(body.get("start") or 0)
    except TypeError, ValueError:
        start = 0
    return EquipmentList(rows, max(total, start + len(rows)), start)


class Session:
    """A connection to the server of one project, after ctsim/hello. ``attached`` = the
    server was running already (it is never stopped by this client)."""

    def __init__(
        self,
        project: str,
        conn: Connection,
        hello: Dict[str, Any],
        location: Optional[EngineLocation] = None,
        server: Optional[StartedServer] = None,
        entry: Optional[ServerEntry] = None,
        root: Optional[str] = None,
    ):
        self.project = project
        #: the root the client asked for ('' = none: the server's own choice)
        self.requested_root = root or ""
        self.conn = conn
        self.hello = hello if isinstance(hello, dict) else {}
        #: the event kinds its hello asked for (None = all; set_events changes it)
        self.events_kinds = viewer_events
        self.location = location
        self.server = server
        self.entry = entry
        self._unsupported: set = set()
        self._closed = False
        self._close_lock = threading.Lock()
        self.clock = clock_from(self.hello)
        self._clock_claimed = False
        self._startup_taken = False

    # -- what it is -----------------------------------------------------------------------
    @property
    def attached(self) -> bool:
        return self.server is None

    @property
    def root(self) -> str:
        """The compiled project the simulation runs as (hello 'root')."""
        return str(self.hello.get("root") or "") or _base_name(self.project)

    @property
    def root_reason(self) -> str:
        """Why that root (engine/PROTOCOL.md rootReason: asked, self, only, lastDatabase,
        firstByName, none); '' when the server does not say."""
        return str(self.hello.get("rootReason") or "")

    @property
    def unit(self) -> List[Tuple[str, str]]:
        """Every project the simulation reads, in its search order: (name, folder) (hello
        'unit')."""
        out = []
        for x in self.hello.get("unit") or ():
            if isinstance(x, dict) and x.get("name"):
                out.append((str(x.get("name")), str(x.get("dir") or "")))
        return out

    @property
    def roots(self) -> List[str]:
        """The compiled projects whose include closure holds the project (hello 'roots')."""
        rs = self.hello.get("roots")
        return [str(x) for x in rs] if isinstance(rs, list) else []

    @property
    def hub_key(self) -> Tuple[str, str]:
        """What the Hub shares it by: (project, root asked)."""
        return (project_key(self.project), root_key(self.requested_root))

    def claim_clock(self) -> bool:
        """True for the first caller only, and only on a server this client started: that
        user sets the simulation's clock from its clock bar; every later user of the shared
        session (another tab, a window the page opened) follows the clock as it is."""
        with self._close_lock:
            if self.attached or self._clock_claimed:
                return False
            self._clock_claimed = True
            return True

    @property
    def version(self) -> str:
        v = self.hello.get("version") or (self.entry.version if self.entry else "")
        return str(v or "")

    @property
    def server_name(self) -> str:
        return str(self.hello.get("server") or self.hello.get("name") or "ctsim")

    @property
    def pid(self) -> int:
        if self.server is not None:
            return self.server.pid
        try:
            return int(self.hello.get("pid") or (self.entry.pid if self.entry else 0))
        except TypeError, ValueError:
            return 0

    @property
    def alive(self) -> bool:
        return not self._closed and not self.conn.closed

    @property
    def closing(self) -> bool:
        """close() was called (it may still be stopping the server)."""
        return self._closed

    def describe(self) -> str:
        """'ctsim 0.3.0' (the server's name and version)."""
        return ("%s %s" % (self.server_name, self.version)).strip()

    def supports(self, name: str) -> Optional[bool]:
        """True / False when the server said (its hello lists its requests, or a request
        was refused as unknown), None when it did not say."""
        cmd = command(name)
        if cmd in self._unsupported:
            return False
        listed = self.hello.get("requests") or self.hello.get("commands")
        if isinstance(listed, list):
            names = {command(str(x)) for x in listed}
            return cmd in names
        # (what a simulator has follows from its protocol revision: ctgedit never uses one
        # below PROTOCOL_REVISION, attached or started; the hello's capabilities are not read)
        return None

    # -- requests ---------------------------------------------------------------------------
    def request(self, name: str, arguments: Any = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
        cmd = command(name)
        if cmd in self._unsupported:
            raise NotAvailable(
                cmd, "%s is not available in this Cicode simulator yet." % (_short_command(cmd))
            )
        try:
            return self.conn.request(cmd, arguments, timeout)
        except NotAvailable:
            self._unsupported.add(cmd)
            raise

    def request_async(
        self, name: str, arguments: Any = None, callback=None, timeout: float = DEFAULT_TIMEOUT
    ) -> PendingRequest:
        cmd = command(name)
        if cmd in self._unsupported:
            raise NotAvailable(
                cmd, "%s is not available in this Cicode simulator yet." % (_short_command(cmd))
            )

        def done(body, err):
            if isinstance(err, NotAvailable):
                self._unsupported.add(cmd)
            if callback is not None:
                callback(body, err)

        return self.conn.request_async(cmd, arguments, done, timeout)

    def load(
        self,
        project: Optional[str] = None,
        page: Optional[str] = None,
        scenario: Optional[str] = None,
        timeout: float = LOAD_TIMEOUT,
        startup: Optional[str] = None,
    ) -> Any:
        return self.request("load", self.load_args(project, page, scenario, startup), timeout)

    def load_args(
        self,
        project: Optional[str] = None,
        page: Optional[str] = None,
        scenario: Optional[str] = None,
        startup: Optional[str] = None,
        start_page: bool = False,
        fast_startup: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Arguments for ``load``."""
        args: Dict[str, Any] = {"project": project or self.project}
        if self.requested_root:
            args["root"] = self.requested_root
        if page:
            args["page"] = page
        if scenario:
            args["scenario"] = scenario
        if startup:
            args["startup"] = startup
        if start_page:
            args["startPage"] = True
        if fast_startup is False:
            # (the startup code at the clock's own speed; the default is fast)
            args["fastStartup"] = False
        return args

    def navigate(
        self,
        page: str,
        window: Optional[int] = None,
        how: Optional[str] = None,
        model: Optional[dict] = None,
        timeout: float = LOAD_TIMEOUT,
    ) -> Dict[str, Any]:
        """``ctsim/navigate``: show a page the way the project's navigation would."""
        args: Dict[str, Any] = {"page": page}
        if window is not None:
            args["window"] = int(window)
        if how:
            args["how"] = how
        if model is not None:
            args["model"] = model
        body = self.request("navigate", args, timeout)
        return body if isinstance(body, dict) else {}

    def take_startup(self) -> bool:
        """True for the first caller only, and only on a server this client started: that
        caller asks for the project's startup code (load {startup: 'auto'}) once; never on a
        server it attached to (whoever started that one decides)."""
        with self._close_lock:
            if self.attached or self._startup_taken:
                return False
            self._startup_taken = True
            return True

    def return_startup(self) -> None:
        """The caller of :meth:`take_startup` could not ask for it (its load failed): the next
        caller may."""
        with self._close_lock:
            self._startup_taken = False

    # -- alarms, equipment and dialogs --------------------------------
    @staticmethod
    def alarms_args(
        filter: Optional[str] = None,
        equipment: Optional[str] = None,
        page: Optional[str] = None,
        window: Optional[int] = None,
        start: int = 0,
        count: Optional[int] = None,
        regex: bool = False,
    ) -> Dict[str, Any]:
        """``alarms {filter?, regex?, equipment?, page?, window?, start?, count?}``."""
        args: Dict[str, Any] = {}
        if filter:
            args["filter"] = filter
            if regex:
                args["regex"] = True
        if equipment is not None:
            args["equipment"] = equipment
        if window is not None:
            args["window"] = int(window)
        elif page:
            args["page"] = page
        if start:
            args["start"] = int(start)
        if count is not None:
            args["count"] = int(count)
        return args

    def alarms(
        self,
        filter: Optional[str] = None,
        equipment: Optional[str] = None,
        page: Optional[str] = None,
        window: Optional[int] = None,
        start: int = 0,
        count: Optional[int] = None,
        timeout: float = TAGS_TIMEOUT,
    ) -> "AlarmList":
        """Configured alarms with their state (``ctsim/alarms``); ``page`` / ``window`` = the
        alarms that page reads and those of its equipment."""
        body = self.request(
            "alarms", self.alarms_args(filter, equipment, page, window, start, count), timeout
        )
        return alarm_list_from(body)

    def equipment(
        self,
        filter: Optional[str] = None,
        start: int = 0,
        count: Optional[int] = None,
        timeout: float = TAGS_TIMEOUT,
    ) -> "EquipmentList":
        """Equipment with its runtime fields MODE / STATE / DRMODE (``ctsim/equipment``)."""
        body = self.request("equipment", self.equipment_args(filter, start, count), timeout)
        return equipment_list_from(body)

    @staticmethod
    def equipment_args(
        filter: Optional[str] = None, start: int = 0, count: Optional[int] = None, regex: bool = False
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {}
        if filter:
            args["filter"] = filter
            if regex:
                args["regex"] = True
        if start:
            args["start"] = int(start)
        if count is not None:
            args["count"] = int(count)
        return args

    @staticmethod
    def set_alarm_args(name: str, state: Any) -> Dict[str, Any]:
        """``setAlarm {name, state}``: a word of ALARM_STATES or an object {on?, ack?, ...}."""
        return {"name": str(name), "state": state}

    def set_alarm(self, name: str, state: Any, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        body = self.request("setAlarm", self.set_alarm_args(name, state), timeout)
        a = body.get("alarm") if isinstance(body, dict) else None
        return a if isinstance(a, dict) else {}

    @staticmethod
    def set_equipment_args(name: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """``setEquipment {name, fields: {MODE?, STATE?, DRMODE?}}`` (values as text)."""
        return {"name": str(name), "fields": {str(k): "" if v is None else str(v) for k, v in fields.items()}}

    def set_equipment(
        self, name: str, fields: Dict[str, Any], timeout: float = DEFAULT_TIMEOUT
    ) -> Dict[str, Any]:
        body = self.request("setEquipment", self.set_equipment_args(name, fields), timeout)
        f = body.get("fields") if isinstance(body, dict) else None
        return dict(f) if isinstance(f, dict) else {}

    @staticmethod
    def answer_args(
        dialog_id: Any,
        choice: Optional[int] = None,
        ok: Optional[bool] = None,
        text: Optional[str] = None,
        values: Optional[Sequence[str]] = None,
        **extra,
    ) -> Dict[str, Any]:
        """``answer {id, choice?, ok?, text?, values?, press?}`` for a ctsim/dialog
        (``press`` in ``extra``: a form's button field pressed, its ``index``)."""
        args: Dict[str, Any] = {"id": dialog_id}
        if choice is not None:
            args["choice"] = int(choice)
        if ok is not None:
            args["ok"] = bool(ok)
        if text is not None:
            args["text"] = str(text)
        if values is not None:
            # (None = a field the answer leaves as it is: a prompt, a button)
            args["values"] = [None if v is None else str(v) for v in values]
        for k, v in extra.items():
            if v is not None:
                args[k] = v
        return args

    def tags(
        self,
        filter: Optional[str] = None,
        page: Optional[str] = None,
        start: int = 0,
        count: int = TAG_PAGE,
        timeout: float = TAGS_TIMEOUT,
        names: Optional[Sequence[str]] = None,
        equipment: Optional[str] = None,
        window: Optional[int] = None,
    ) -> TagPage:
        """A page of the tag listing."""
        args = self.tag_args(filter, page, start, count, window)
        if names:
            args["names"] = list(names)
        if equipment is not None:
            args["equipment"] = equipment
        return tag_page_from(self.request("tags", args, timeout), start)

    @staticmethod
    def tag_args(
        filter: Optional[str],
        page: Optional[str],
        start: int,
        count: int,
        window: Optional[int] = None,
        regex: bool = False,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"start": int(start), "count": int(count)}
        if filter:
            args["filter"] = filter
            if regex:
                args["regex"] = True  # (capability regexFilter)
        if page:
            args["page"] = page
        if window is not None:
            args["window"] = int(window)
        return args

    def all_tags(
        self,
        filter: Optional[str] = None,
        page: Optional[str] = None,
        limit: Optional[int] = None,
        page_size: int = TAG_PAGE,
        timeout: float = TAGS_TIMEOUT,
        window: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Every tag (up to ``limit``), page by page; (rows, total)."""
        rows: List[Dict[str, Any]] = []
        start, total = 0, None
        while True:
            want = page_size if limit is None else min(page_size, limit - len(rows))
            if want <= 0:
                break
            tp = self.tags(filter, page, start, want, timeout, window=window)
            total = tp.total
            rows.extend(tp.tags)
            start += len(tp.tags)
            if not tp.tags or start >= tp.total:
                break
        return rows, total if total is not None else len(rows)

    @staticmethod
    def subscribe_args(
        names: Sequence[str], replace: bool = True, page: Optional[str] = None
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"names": list(names), "replace": bool(replace)}
        if page:
            args["page"] = page
        return args

    def subscribe(
        self,
        names: Sequence[str],
        replace: bool = True,
        page: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> List[Dict[str, Any]]:
        """Ask for tagsChanged events of these tags (``replace``: instead of the ones asked
        before; ``page``: also every tag the compiled page reads); their values now."""
        body = self.request("subscribe", self.subscribe_args(names, replace, page), timeout)
        return tag_changes_from(body)

    @staticmethod
    def set_tag_args(
        name: str, value: Any, quality: Optional[str] = None, type: Optional[str] = None
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"name": name, "value": value}
        if quality:
            args["quality"] = quality
        if type:
            # the type of the simulated tag a write makes for an undefined name
            args["type"] = type
        return args

    def set_tag(
        self,
        name: str,
        value: Any,
        quality: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write a tag; its value after the write ``{name, value, quality}`` (the server
        refuses an unknown tag or a bad value with the runtime's error; with the option
        simulateUndefined on, an undefined name gets a simulated tag of ``type``)."""
        body = self.request("setTag", self.set_tag_args(name, value, quality, type), timeout)
        return written_tag(body, name)

    def options(
        self, simulate_undefined: Optional[bool] = None, timeout: float = DEFAULT_TIMEOUT
    ) -> Dict[str, Any]:
        """The server's simulator-only options (``ctsim/options``); ``simulate_undefined``
        sets whether a setTag of an undefined name makes a simulated tag (None = only ask).
        The body ``{simulateUndefined}``."""
        args: Dict[str, Any] = {}
        if simulate_undefined is not None:
            args["simulateUndefined"] = bool(simulate_undefined)
        body = self.request("options", args, timeout)
        return body if isinstance(body, dict) else {}

    def call_outcome(self, expr: str, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """Run Cicode text (one function call with literal arguments, as ctCicode); the
        whole outcome ``{task, state, result, value, type, error, errorText?, haltReason?,
        notSimulated?, timedOut?}`` (the engine's CallOutcome)."""
        body = self.request("call", {"expr": expr}, timeout)
        return body if isinstance(body, dict) else {"value": body}

    def call(self, expr: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
        """Run Cicode text; its value. A call whose task halted (a hardware error) raises
        RequestFailed with the error's text."""
        body = self.call_outcome(expr, timeout)
        if str(body.get("state") or "").lower() == "halt":
            code = body.get("error")
            text = body.get("errorText") or body.get("haltReason") or "the call halted"
            if code:
                text = "%s (hardware error %s)" % (text, code)
            raise RequestFailed(command("call"), str(text), body)
        if body.get("value") is None and "result" in body:
            return body.get("result")
        return body.get("value")

    def clock_request(
        self, mode: str = "", value: Optional[float] = None, timeout: float = DEFAULT_TIMEOUT
    ) -> ClockInfo:
        """run, pause, step (``value`` = seconds, default the step size), speed (``value`` x
        real time), or '' = only ask; the clock after it."""
        body = self.request("clock", self.clock_args(mode, value), timeout)
        self.clock = clock_from(body, self.clock)
        return self.clock

    @staticmethod
    def clock_args(mode: str, value: Optional[float] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {}
        if mode:
            args["mode"] = mode
        if value is not None:
            args["value"] = value
        return args

    def scenario(self, action: str, path: str = "", timeout: float = LOAD_TIMEOUT) -> Any:
        """'load', 'run' (load + run) or 'stop' a scenario file (scenarios/README.md)."""
        args: Dict[str, Any] = {"action": action}
        if path:
            args["path"] = path
        return self.request("scenario", args, timeout)

    def open_page(self, name: str, window: Optional[int] = None, timeout: float = LOAD_TIMEOUT) -> int:
        """Display a page in a simulated window; the window number."""
        args: Dict[str, Any] = {"name": name}
        if window is not None:
            args["window"] = int(window)
        body = self.request("openPage", args, timeout)
        try:
            return int((body or {}).get("window", window if window is not None else 0))
        except TypeError, ValueError:
            return int(window or 0)

    def reload_file(
        self, file: str, callback=None, text: Optional[str] = None, timeout: float = LOAD_TIMEOUT
    ) -> "PendingRequest":
        """``ctsim/reload {file, text?}``: a .ci file of the unit compiled again and the program
        swapped (a running task keeps its code). The answer ``{accepted, file, compiled,
        incremental, ms, reason?, errors: [{code, line, column, message}]}`` goes to
        ``callback(body, err)`` (the connection's reader thread)."""
        args: Dict[str, Any] = {"file": file}
        if text is not None:
            args["text"] = text
        return self.request_async("reload", args, callback=callback, timeout=timeout)

    @staticmethod
    def open_page_args(
        name: str,
        window: Optional[int] = None,
        model: Optional[dict] = None,
        associations: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """``openPage {name, window?, model?, associations?}`` (engine/PROTOCOL.md: the model =
        sim/pagemodel's ctsim-page/0 export: its drawing summary, and for a page that is not
        current its rows, which the engine then compiles and runs)."""
        args: Dict[str, Any] = {"name": name}
        if window is not None:
            args["window"] = int(window)
        if model is not None:
            args["model"] = model
        if associations:
            args["associations"] = {str(k): str(v) for k, v in associations.items()}
        return args

    def open_page_full(
        self,
        name: str,
        window: Optional[int] = None,
        model: Optional[dict] = None,
        associations: Optional[Dict[str, str]] = None,
        timeout: float = LOAD_TIMEOUT,
    ) -> Dict[str, Any]:
        """openPage; the whole answer ``{window, page, error, source ('rdb' | 'rows' |
        'projectRows'), notes, state (PAGE: the first states)}``."""
        body = self.request("openPage", self.open_page_args(name, window, model, associations), timeout)
        return body if isinstance(body, dict) else {}

    def close_page(self, window: int, timeout: float = DEFAULT_TIMEOUT) -> Any:
        """closePage: the page's close events run (EXIT_FUNC ...); a window other than 0 is
        freed."""
        return self.request("closePage", {"window": int(window)}, timeout)

    def page_model(self, window: int, model: dict, timeout: float = LOAD_TIMEOUT) -> Any:
        """pageModel: the drawing summary of the page a window shows (one Cicode opened)."""
        return self.request("pageModel", {"window": int(window), "model": model}, timeout)

    def windows(self, timeout: float = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
        """The open windows ``[{window, page, title, x, y, w, h, mode, parent, active,
        hasModel, path}]`` (the windowsChanged body)."""
        body = self.request("windows", {}, timeout)
        return windows_from(body)

    def page_state(self, window: int, timeout: float = DEFAULT_TIMEOUT):
        """The window's full object states (a PageState with replace=True)."""
        from .state import page_state_from_json

        body = self.request("pageState", {"window": int(window)}, timeout)
        st = page_state_from_json(body, replace=True)
        if not (isinstance(body, dict) and "window" in body):
            st.window = int(window)
        return st

    @staticmethod
    def input_args(window: int, event: Any) -> Dict[str, Any]:
        """``input {window, an, kind, key?, x?, y?}`` from a sources.InputEvent (its other
        fields go along: the AN chain, the drag offset, the modifiers, the button)."""
        g = (
            (lambda k, d=None: getattr(event, k, d))
            if not isinstance(event, dict)
            else (lambda k, d=None: event.get(k, d))
        )
        kind = str(g("kind", "down") or "down")
        # the engine's names (PROTOCOL.md: down | up | click | key | dblclick | drag | move)
        args: Dict[str, Any] = {"window": int(window), "kind": INPUT_KINDS.get(kind, kind)}
        an = g("an")
        if an:
            args["an"] = int(an)
        ans = g("ans") or ()
        if ans:
            args["ans"] = [int(a) for a in ans]
        args["x"], args["y"] = float(g("x", 0.0) or 0.0), float(g("y", 0.0) or 0.0)
        if args["kind"] == "drag":
            args["dx"], args["dy"] = float(g("dx", 0.0) or 0.0), float(g("dy", 0.0) or 0.0)
        if args["kind"] == "key":
            args["key"] = g("key", "") or ""
            if g("text"):
                args["text"] = g("text")
        mods = int(g("modifiers", 0) or 0)
        if mods:
            args["modifiers"] = mods
        btn = g("button", "left")
        if btn and btn != "left" and args["kind"] != "key":
            args["button"] = btn
        return args

    # -- events ---------------------------------------------------------------------------
    def add_listener(self, fn: Listener) -> None:
        self.conn.add_listener(fn)

    def remove_listener(self, fn: Listener) -> None:
        self.conn.remove_listener(fn)

    def on_close(self, fn) -> None:
        self.conn.on_close(fn)

    def remove_close_listener(self, fn) -> None:
        self.conn.remove_close_listener(fn)

    def close(self, fast: bool = False) -> None:
        """Close the connection; a server this client started is stopped, an attached one
        keeps running. ``fast`` (application exit): no waiting for queued messages or for
        the server to end by itself. Any thread; a second call returns at once."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        if self.server is not None and not fast and self.server.running() and not self.conn.closed:
            others = self.other_clients()
            if others:
                # another viewer or a VS Code debugger joined the server ctgedit started:
                # it stays (it still ends
                # with ctgedit, its job object); only this connection goes. No answer (-1): a
                # debugger holds it in page code (stopping it would end VS Code's session)
                if others < 0:
                    log.info(
                        "ctsim: left the server pid %d running: it does not answer (a debugger may hold it)",
                        self.server.pid,
                    )
                else:
                    log.info(
                        "ctsim: left the server pid %d running for %d other client(s)",
                        self.server.pid,
                        others,
                    )
                self.conn.close(drain=1.0)
                return
        if self.server is not None:
            # from now on nobody in this process attaches to it (it is going away)
            _stopping_add(self.server.pid)
        grace = 0.0 if fast else 0.5
        if self.server is not None and self.server.running() and not self.conn.closed:
            # ask a server of our own to end (it removes its discovery file itself)
            try:
                p = self.conn.request_async("shutdown", {}, timeout=1.0)
                if not self.conn.on_reader_thread():
                    p.wait(0.2 if fast else 1.0)
                grace = 0.5 if fast else 3.0
            except CtsimError:
                pass
        self.conn.close(drain=0.0 if fast else 1.0)
        if self.server is not None:
            self.server.stop(timeout=0.5 if fast else 3.0, grace=grace)

    def other_clients(self, timeout: float = 1.0) -> int:
        """How many other connections the server has (its hello: ``clients`` counts this one
        too; an attached debugger counts at least one); 0 when it does not say; -1 when it
        does not answer in time: a debugger may hold the simulation in page code (every
        other request waits), so the server is not ours alone to stop."""
        if self.conn.on_reader_thread():
            return 0  # no answer could arrive meanwhile
        try:
            body = self.conn.request("hello", {"client": CLIENT, "protocol": PROTOCOL}, timeout)
        except RequestTimeout:
            return -1
        except CtsimError:
            return 0
        if not isinstance(body, dict):
            return 0
        try:
            n = max(0, int(body.get("clients") or 0) - 1)
        except TypeError, ValueError:
            n = 0
        return max(n, 1) if body.get("debugger") is True else n

    #: the definition fields ctsim/setTagProperty overrides
    TAG_PROPERTY_FIELDS = (
        "ENG_ZERO",
        "ENG_FULL",
        "RAW_ZERO",
        "RAW_FULL",
        "ENG_UNITS",
        "FORMAT",
        "COMMENT",
        "DEADBAND",
        "UNIT",
        "ADDR",
        "CUSTOM1",
        "CUSTOM2",
        "CUSTOM3",
        "CUSTOM4",
        "CUSTOM5",
        "CUSTOM6",
        "CUSTOM7",
        "CUSTOM8",
    )

    @staticmethod
    def tag_property_args(
        name: str, field: Optional[str] = None, value: Optional[str] = None
    ) -> Dict[str, Any]:
        """ctsim/setTagProperty {name, field?, value?}: ``value`` None = the field back to the
        project's; ``field`` None = every field back."""
        args: Dict[str, Any] = {"name": name}
        if field:
            args["field"] = field
            if value is not None:
                args["value"] = str(value)
        return args

    def set_events(self, kinds: Optional[Sequence[str]], timeout: float = 5.0) -> bool:
        """Ask for these event kinds only (None = all): a second ctsim/hello with the same
        client fields (the engine replaces the client's kinds). False when it failed."""
        args: Dict[str, Any] = {"client": CLIENT, "protocol": PROTOCOL, "pid": os.getpid()}
        args.update(VIEWER_FEATURES)
        if kinds is not None:
            args["events"] = list(kinds)
        root = self.hello.get("root")
        if root:
            args["root"] = root
        try:
            self.conn.request("hello", args, timeout)
            self.events_kinds = tuple(kinds) if kinds is not None else None
            return True
        except Exception:  # noqa: BLE001 -- the events keep coming as before
            return False

    def stop_reason(self) -> str:
        """Why the connection ended, with the exit code and last output of a server this
        client started."""
        text = self.conn.close_reason or "The connection to the Cicode simulator closed."
        if self.server is not None:
            code = self.server.proc.poll()
            if code is not None:
                text += " The simulator ended (exit code %d)." % code
                tail = self.server.tail(3)
                if tail:
                    text += "\n" + tail
        return text


def _hello(
    conn: Connection, timeout: float, root: Optional[str] = None, viewer: bool = False
) -> Dict[str, Any]:
    try:
        from .. import __version__ as version
    except Exception:  # noqa: BLE001
        version = ""
    args: Dict[str, Any] = {"client": CLIENT, "version": version, "protocol": PROTOCOL, "pid": os.getpid()}
    if viewer:
        # only a client that answers them says so: the engine holds a Cicode-opened page's
        # start for its drawing (3 s) and a waiting task for a dialog's answer (60 s)
        args.update(VIEWER_FEATURES)
        if viewer_events is not None:
            args["events"] = list(viewer_events)
    if root:
        args["root"] = root  # a server of another root refuses the hello
    try:
        body = conn.request("hello", args, timeout)
    except NotAvailable:
        conn.close()
        raise ServerUnavailable("This Cicode simulator does not take viewers (ctsim/hello is unknown to it).")
    except CtsimError:
        conn.close()
        raise
    return body if isinstance(body, dict) else {}


def connect(
    entry: ServerEntry,
    timeout: float = CONNECT_TIMEOUT,
    hello_timeout: float = HELLO_TIMEOUT,
    root: Optional[str] = None,
    viewer: bool = False,
) -> Tuple[Connection, Dict[str, Any]]:
    """Connect to a discovered server and say hello (``root``: the root it must run as;
    ``viewer``: hello {models, dialogs}, see :func:`open_session`)."""
    stream = connect_stream(entry.transport, entry.address, timeout)
    conn = Connection(stream, entry.id or entry.address).start()
    return conn, _hello(conn, hello_timeout, root, viewer)


def _hello_root(hello: Dict[str, Any], entry: Optional[ServerEntry], project: str) -> str:
    """The root a server said it runs as (hello 'root')."""
    r = hello.get("root") if isinstance(hello, dict) else None
    return str(r) if r else _base_name(project)


def open_session(
    project: str,
    engine_path: Optional[str] = None,
    location: Optional[EngineLocation] = None,
    folder: Optional[str] = None,
    start: bool = True,
    start_timeout: float = START_TIMEOUT,
    hello_timeout: float = HELLO_TIMEOUT,
    load_timeout: float = LOAD_TIMEOUT,
    root: Optional[str] = None,
    viewer: bool = False,
) -> Session:
    """Attach to a running server for ``project``, or start one."""
    project = os.path.abspath(project)
    root = (root or "").strip() or None
    for e in servers_for(project, folder, root):
        if stopping(e.pid):
            log.info("ctsim: skipped the server %s (it is being stopped)", e.path or e.address)
            continue
        try:
            conn, hello = connect(e, hello_timeout=hello_timeout, root=root, viewer=viewer)
        except CtsimError as exc:
            log.info("ctsim: skipped the server %s (%s)", e.path or e.address, exc)
            continue
        rev = protocol_revision(hello)
        if rev < PROTOCOL_REVISION:
            # another protocol (another ctgedit's simulator, a VS Code extension's)
            log.info(
                "ctsim: skipped the server %s (pid %d, %s): protocol revision %d, this ctgedit needs %d",
                e.path or e.address,
                e.pid,
                process_exe(e.pid) or "?",
                rev,
                PROTOCOL_REVISION,
            )
            conn.close()
            continue
        have = _hello_root(hello, e, project)
        if root and root_key(have) != root_key(root):
            log.info("ctsim: skipped the server %s (it runs as %s, not %s)", e.path or e.address, have, root)
            conn.close()
            continue
        log.info(
            "ctsim: attached to %s (pid %d, %s) for %s as %s",
            e.address,
            e.pid,
            process_exe(e.pid) or "?",
            project,
            have,
        )
        return Session(project, conn, hello, entry=e, root=root)
    if not start:
        raise ServerUnavailable("No Cicode simulator runs for this project.")
    loc = location or find_engine(engine_path)
    srv = StartedServer.start(loc, project, root)
    try:
        entry = srv.wait_announced(folder, start_timeout)
        deadline = time.monotonic() + max(5.0, CONNECT_TIMEOUT)
        while True:
            try:
                conn, hello = connect(entry, hello_timeout=hello_timeout, root=root, viewer=viewer)
                break
            except ServerUnavailable:
                if not srv.running() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        rev = protocol_revision(hello)
        if rev < PROTOCOL_REVISION:
            conn.close()
            raise ServerUnavailable(
                "This ctsim.exe speaks protocol revision %d; ctgedit needs %d. "
                "Choose the simulator installed with ctgedit (Choose Simulator)." % (rev, PROTOCOL_REVISION)
            )
        s = Session(project, conn, hello, location=loc, server=srv, entry=entry, root=root)
        runs = _hello_root(s.hello, entry, project)
        if root and root_key(runs) != root_key(root):
            raise ServerUnavailable(
                "The Cicode simulator runs %s as part of %s, not %s." % (_base_name(project), runs, root)
            )
        have = s.hello.get("project")
        if (have and project_key(str(have)) != project_key(project)) or s.hello.get("loaded") is False:
            s.load(project, timeout=load_timeout)
        return s
    except BaseException:
        srv.stop()
        raise


# -- sharing sessions between the tabs of a process --------------------------------------------
class Hub:
    """One session per project for the whole process, counted by its users. The last user's
    release stops a started server after ``linger`` seconds (a page simulated again soon
    reuses it); :meth:`shutdown` stops everything at once (application exit)."""

    def __init__(self, opener: Callable[..., Session] = open_session, linger: float = LINGER_S):
        self._opener = opener
        self.linger = linger
        self._lock = threading.Lock()
        self._sessions: Dict[Tuple[str, str], Session] = {}
        self._refs: Dict[Tuple[str, str], int] = {}
        self._timers: Dict[Tuple[str, str], threading.Timer] = {}
        self._opening: Dict[Tuple[str, str], threading.Lock] = {}

    def acquire(self, project: str, root: Optional[str] = None, **kw) -> Session:
        """The session of ``project`` run as ``root`` (None = the server's choice; a new one
        when there is none or it closed). Blocks while a server starts: call it on a worker
        thread."""
        root = (root or "").strip() or None
        key = (project_key(project), root_key(root))
        with self._lock:
            gate = self._opening.setdefault(key, threading.Lock())
        with gate:
            dead = None
            with self._lock:
                s = self._sessions.get(key)
                if s is not None and not s.alive:
                    # its connection ended: close it now (a server it started may still run
                    # with a broken link; left alone it would be attached to below as a
                    # stranger and never stopped) and forget its linger timer
                    self._sessions.pop(key, None)
                    self._refs.pop(key, None)
                    t = self._timers.pop(key, None)
                    if t is not None:
                        t.cancel()
                    dead, s = s, None
                if s is not None:
                    t = self._timers.pop(key, None)
                    if t is not None:
                        t.cancel()
                    self._refs[key] = self._refs.get(key, 0) + 1
                    return s
            if dead is not None:
                try:
                    dead.close(fast=True)
                except Exception:  # noqa: BLE001 -- the old one must not stop the new one
                    logsetup.note_exception("sim.client.hub_dead_close")
            s = self._opener(project, root=root, **kw)
            with self._lock:
                self._sessions[key] = s
                self._refs[key] = 1
            return s

    def prestart(self, project: str, root: Optional[str] = None, **kw) -> None:
        """Open the session of ``project`` on a worker thread and let it linger: the server
        boots while the caller does other work, and the next :meth:`acquire` (it waits for
        an opening in progress) gets it. Failures are left to that acquire."""

        def run():
            try:
                s = self.acquire(project, root=root, **kw)
            except Exception:  # noqa: BLE001 -- the real acquire reports it
                return
            self.release(s)

        gcguard.before_worker()
        threading.Thread(target=run, daemon=True, name="ctsim-prestart").start()

    def release(self, session: Session, linger: Optional[float] = None) -> None:
        key = session.hub_key
        with self._lock:
            if self._sessions.get(key) is not session:
                drop = True
            else:
                n = self._refs.get(key, 1) - 1
                self._refs[key] = n
                drop = False
                if n > 0:
                    return
        if drop:
            # not the hub's any more (replaced, or let go by close_started): a close can take
            # seconds (hello, shutdown, the process ending) and the caller may be the GUI
            # thread (a tab that stops): closed on a worker, never in the caller
            if not session.closing:
                gcguard.before_worker()
                threading.Thread(
                    target=self._close_quietly, args=(session,), daemon=True, name="ctsim-close"
                ).start()
            return
        wait = self.linger if linger is None else linger
        if wait <= 0:
            self._close_key(key, session)
            return
        gcguard.before_worker()
        t = threading.Timer(wait, self._close_key, (key, session))
        t.daemon = True
        with self._lock:
            old = self._timers.pop(key, None)
            self._timers[key] = t
        if old is not None:
            old.cancel()
        t.start()

    def _close_key(self, key: Tuple[str, str], session: Session) -> None:
        with self._lock:
            if self._sessions.get(key) is session and self._refs.get(key, 0) <= 0:
                self._sessions.pop(key, None)
                self._refs.pop(key, None)
                self._timers.pop(key, None)
            elif self._sessions.get(key) is session:
                return
        session.close()

    def sessions(self) -> List[Session]:
        with self._lock:
            return list(self._sessions.values())

    def close_started(self, project: Optional[str] = None) -> int:
        """Let go of every server this process started."""
        key = project_key(project) if project else None
        with self._lock:
            mine = [
                (k, s)
                for k, s in self._sessions.items()
                if s.server is not None and (key is None or k[0] == key)
            ]
            for k, s in mine:
                self._sessions.pop(k, None)
                self._refs.pop(k, None)
                t = self._timers.pop(k, None)
                if t is not None:
                    t.cancel()
        for _k, s in mine:
            _stopping_add(s.server.pid)
        if mine:
            gcguard.before_worker()
            threading.Thread(
                target=self._close_all, args=([s for _k, s in mine],), daemon=True, name="ctsim-close"
            ).start()
        return len(mine)

    @staticmethod
    def _close_quietly(session: Session) -> None:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            logsetup.note_exception("sim.client.hub_close")

    @staticmethod
    def _close_all(sessions: List[Session]) -> None:
        for s in sessions:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                logsetup.note_exception("sim.client.close_started")

    def shutdown(self, fast: bool = False) -> None:
        """Close every session now (``fast`` at application exit)."""
        with self._lock:
            sessions = list(self._sessions.values())
            timers = list(self._timers.values())
            self._sessions.clear()
            self._refs.clear()
            self._timers.clear()
        for t in timers:
            t.cancel()
        for s in sessions:
            try:
                s.close(fast=fast)
            except Exception:  # noqa: BLE001
                pass


HUB = Hub()
atexit.register(HUB.shutdown, True)


def disabled() -> bool:
    """CTG_NO_SIM_ENGINE=1: the Simulate window does not use the simulator (the tests)."""
    return os.environ.get(DISABLE_ENV, "") not in ("", "0")
