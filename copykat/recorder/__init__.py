"""The recorder layer: parse a PTY stream and serve the buffer over a socket.

``record`` spawns a command under a pseudo-terminal (:mod:`.proxy`), parses its
outbound stream into a ring buffer of :class:`Message` objects (:mod:`.parser`),
and serves that buffer to viewers over a per-terminal Unix socket
(:mod:`.server`).
"""

from __future__ import annotations

from .parser import (
    _VIEW_BEGIN,
    _VIEW_END,
    Message,
    PtyRecorder,
    strip_ansi,
)
from .proxy import _run_interactive, _run_piped, _with_shell_integration
from .server import RecorderServer, _recorder_alive, _request, request_buffer

__all__ = [
    "Message",
    "PtyRecorder",
    "strip_ansi",
    "RecorderServer",
    "request_buffer",
]
