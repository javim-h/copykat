"""Socket naming shared across the recorder, viewer and CLI commands.

Each terminal's recorder owns a distinct Unix socket named after the tty it's
attached to (``copykat-pts-3.sock``), so several recordings can run at once and a
viewer can pick which one to attach to.
"""

from __future__ import annotations

import glob
import os
import tempfile

_SOCKET_GLOB = os.path.join(tempfile.gettempdir(), "copykat-*.sock")
# Exported into the shell that `record` spawns, so `copykat viewer`/`stop` run
# *inside* a recorded session know which recorder they belong to.
_SOCKET_ENV = "COPYKAT_SOCKET"


def _terminal_id(fd: int = 0) -> str:
    """A stable id for the terminal *fd* is attached to (e.g. ``pts-3``).

    Lets each terminal's recorder own a distinct socket, so several recordings
    can run at once and a viewer can pick which one to attach to.
    """
    try:
        name = os.ttyname(fd)  # e.g. /dev/pts/3
    except OSError:
        return f"pid{os.getpid()}"
    return name.replace("/dev/", "").replace("/", "-")


def _default_socket(fd: int = 0) -> str:
    return os.path.join(tempfile.gettempdir(), f"copykat-{_terminal_id(fd)}.sock")


def _socket_term_name(path: str) -> str:
    """Recover the terminal id from a socket path: copykat-pts-3.sock -> pts-3."""
    base = os.path.basename(path)
    if base.startswith("copykat-") and base.endswith(".sock"):
        return base[len("copykat-"):-len(".sock")]
    return base


def _resolve_socket(explicit: str | None) -> tuple[str | None, str | None]:
    """Pick which recorder to attach to, returning (socket_path, error).

    Priority: explicit -s, then the session we're inside ($COPYKAT_SOCKET),
    then this terminal's own recorder, then the only one running, otherwise
    the most recently started one (the current session). Errors only when
    none exist.
    """
    if explicit:
        return explicit, None
    env = os.environ.get(_SOCKET_ENV)
    if env:
        return env, None
    found = sorted(glob.glob(_SOCKET_GLOB))
    if not found:
        return None, "copykat: no recorder found (start one with `copykat record`)"
    if len(found) == 1:
        return found[0], None
    if _default_socket() in found:  # prefer the recorder on this terminal
        return _default_socket(), None

    # Several recorders, none on this terminal: attach to the newest one rather
    # than making the user pass -s.
    def _mtime(p: str) -> float:
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0

    return max(found, key=_mtime), None
