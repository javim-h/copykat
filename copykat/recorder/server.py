"""Request/response server: a viewer connects and asks for the current buffer.

Where the viewer *serves* nothing, the recorder serves its parsed buffer over a
per-terminal Unix socket on request. The protocol is one exchange per
connection (see :class:`RecorderServer`).
"""

from __future__ import annotations

import json
import os
import socket
import threading
from dataclasses import asdict

from ..sockets import _default_socket
from .parser import Message, PtyRecorder

__all__ = ["RecorderServer", "request_buffer"]


def _msg_dict(m: Message) -> dict:
    return asdict(m)


class RecorderServer:
    """Serve the recorder's parsed buffer over a Unix socket, on request.

    Protocol (one exchange per connection):
      * ``get``:  reply ``{"maxlen": N, "messages": [...]}`` and close.
      * ``stop``: reply ``{"stopped": true}``, then invoke ``on_stop`` so the
        recording session shuts down.
    """

    def __init__(
        self,
        rec: PtyRecorder,
        path: str | None = None,
        on_stop=None,
    ) -> None:
        self.rec = rec
        self.path = path or _default_socket()
        self.on_stop = on_stop
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "RecorderServer":
        if os.path.exists(self.path):
            os.unlink(self.path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.path)
        # Owner-only: the buffer holds recorded session I/O. Without this the
        # socket inherits the umask (e.g. 0775 lets the group connect, read the
        # whole buffer, or send `stop`). Don't depend on the umask for that.
        os.chmod(self.path, 0o600)
        s.listen(8)
        self._sock = s
        self._thread = threading.Thread(
            target=self._accept_loop, name="copykat-server", daemon=True
        )
        self._thread.start()
        return self

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break  # socket closed by stop()
            threading.Thread(
                target=self._handle, args=(conn,), daemon=True
            ).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            req = conn.recv(4096).strip()
            if req == b"stop":
                conn.sendall(b'{"stopped": true}\n')
                if self.on_stop is not None:
                    self.on_stop()
                return
            payload = {
                "maxlen": self.rec.maxlen,
                "messages": [_msg_dict(m) for m in self.rec.messages()],
            }
            conn.sendall((json.dumps(payload) + "\n").encode())
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _request(path: str, line: bytes, timeout: float = 2.0) -> bytes:
    """Send a one-line request to a recorder and return the full reply bytes."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall(line)
        chunks = []
        while True:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def request_buffer(path: str | None = None, timeout: float = 2.0) -> dict:
    """Connect to a running recorder and return its current parsed buffer."""
    path = path or _default_socket()
    return json.loads(_request(path, b"get\n", timeout).decode())


def _recorder_alive(path: str) -> bool:
    """True if a recorder is currently serving *path*."""
    try:
        request_buffer(path, timeout=0.5)
        return True
    except OSError:
        return False
