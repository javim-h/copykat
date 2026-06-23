"""copykat: record a terminal session's output to an in-memory ring buffer.

Invoked as ``copykat record``, this spawns a
command (default: your ``$SHELL``, typically bash) under a pseudo-terminal and
records the session's *outbound* stream (everything the terminal prints) on a
background thread. Your keystrokes are forwarded to the shell but never
recorded, so a password typed at a no-echo prompt can't leak into the buffer.

The package is split into three layers, each surfaced as a CLI command:

  * :mod:`copykat.recorder` ``copykat record``: parse a PTY stream into a ring
    buffer of :class:`Message` objects and serve it over a Unix socket.
  * :mod:`copykat.viewer`   ``copykat viewer``: a Textual list of recorded
    commands, polling a recorder's socket live.
  * :mod:`copykat.watcher`  ``copykat listen``: a socket inbox an AI agent
    monitors for messages the viewer pushes at it.
"""

from __future__ import annotations

from .cli import main
from .recorder import Message, PtyRecorder, strip_ansi

__all__ = ["Message", "PtyRecorder", "strip_ansi", "main"]
