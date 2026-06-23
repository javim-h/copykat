"""Discover the ``copykat listen`` inboxes the viewer can send recordings to.

The viewer is always attached to this terminal's own recorder, so there's no
recorder discovery here. The ``s`` key pushes the highlighted recording to a
``copykat listen`` inbox, discovered by its socket name
(``copykat:watcher:<who>:<session>.sock``); when more than one is live the user
picks which agent gets the copy.
"""

from __future__ import annotations

import glob
import json
import os
import socket
import tempfile
import time

from ..watcher.inbox import _inbox_alive

__all__ = ["_live_watchers", "_send_to_watcher", "_short_cwd"]


def _short_cwd() -> str:
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd


# copykat listen inboxes the viewer can push messages to (see copykat.watcher).
_WATCHER_GLOB = os.path.join(tempfile.gettempdir(), "copykat:watcher:*.sock")


def _watcher_sockets() -> list[tuple[str, str, str]]:
    """Discover copykat listen inboxes as (path, who, session) tuples.

    Socket names are ``copykat:watcher:<who>:<session>.sock``: split the middle
    back out so the viewer can show who/which-session each inbox belongs to.
    """
    head, tail = "copykat:watcher:", ".sock"
    out: list[tuple[str, str, str]] = []
    for path in sorted(glob.glob(_WATCHER_GLOB)):
        body = os.path.basename(path)[len(head):-len(tail)]
        who, _, session = body.partition(":")
        out.append((path, who or "?", session or "?"))
    return out


def _live_watchers() -> list[tuple[str, str, str]]:
    """The copykat listen inboxes currently accepting connections.

    The glob can list stale socket files left by crashed listeners, so each
    candidate is probed before being offered as a send target.
    """
    return [t for t in _watcher_sockets() if _inbox_alive(t[0])]


def _send_to_watcher(path: str, text: str, timeout: float = 2.0) -> bool:
    """Send *text* to a watcher as one JSONL line; True on success."""
    line = json.dumps({"from": "copykat", "time": time.time(), "text": text})
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall(line.encode() + b"\n")
        return True
    except OSError:
        return False
