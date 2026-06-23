"""A Unix-socket inbox an AI agent can monitor for copykat messages.

This is the inverse of copykat's :class:`RecorderServer`. Where the recorder
*serves* its buffer on request, this *listens* for messages pushed at it and
echoes each one to stdout, so when an AI agent (Claude Code, etc.) runs
``copykat listen`` as a background process, every message the copykat Textual app
sends shows up in the agent's view.

The socket is keyed by a fixed ``copykat:watcher`` prefix, a *who* (the agent's
identity, e.g. ``claude-code``) and a *session* title, so the copykat app can
list inboxes and show who/which-session each one belongs to:

    /tmp/copykat:watcher:<who>:<session>.sock

The agent only ever runs this watcher; sending is done from the copykat app.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import sys
import tempfile
import time

__all__ = ["watch", "_socket_path"]

# Mirrors copykat's socket naming (``copykat-<terminal>.sock``); here the key is a
# constant prefix plus the agent identity and session title.
_PREFIX = "copykat:watcher"


def _slug(s: str) -> str:
    """Collapse arbitrary text to a safe single filename component."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "default"


def _socket_path(who: str, session: str, directory: str | None = None) -> str:
    """Build the inbox socket path for *who*/*session*."""
    directory = directory or tempfile.gettempdir()
    return os.path.join(directory, f"{_PREFIX}:{_slug(who)}:{_slug(session)}.sock")


def _emit(line: str) -> None:
    """Write one received message to stdout, flushed so the agent sees it live."""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _attach_lifetime(path: str, listen_fd: int) -> int:
    """Tie *path*'s removal to this process's death, even under SIGKILL.

    Forks a sentinel child that blocks on a pipe whose write end the caller
    holds. When this process exits for any reason (SIGKILL and hard crashes
    included; no Python handler can catch those), the kernel closes that fd,
    the sentinel wakes, and (after confirming no live watcher has rebound the
    socket) unlinks *path*. On a graceful exit ``watch``'s ``finally`` unlinks
    first; the sentinel's probe then finds nothing to remove.

    Returns the pipe write fd: keep it open for the process's whole life.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # ---- sentinel child ----
        os.close(write_fd)
        # Drop our dup of the listen socket so we neither keep its endpoint
        # alive nor fool _inbox_alive's connect probe into seeing a live watcher.
        os.close(listen_fd)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        try:
            os.read(read_fd, 1)  # blocks until the parent dies
        except OSError:
            pass
        os.close(read_fd)
        # Only remove a truly dead socket: a fresh watcher may have rebound the
        # same who/session between the parent's death and now.
        if not _inbox_alive(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        os._exit(0)
    # ---- parent ----
    os.close(read_fd)
    return write_fd


def watch(path: str, as_json: bool) -> int:
    """Bind *path* and print every newline-delimited message pushed to it."""
    if os.path.exists(path):
        # A stale socket from a crashed watcher would block bind(); only clear
        # it if nobody is actually listening there.
        if _inbox_alive(path):
            sys.stderr.write(
                f"copykat listen: already watching {path}\n"
                f"          (another watcher owns this who/session)\n"
            )
            return 1
        os.unlink(path)

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    # Owner-only: messages may carry session content. Don't inherit the umask
    # (same reasoning as copykat's RecorderServer).
    os.chmod(path, 0o600)
    s.listen(8)

    # Attach the socket's lifetime to this process: the sentinel unlinks *path*
    # when we die, including under SIGKILL or a hard crash, which the signal
    # handlers and `finally` below can't catch. The stale-socket check at
    # startup (above) stays as a backstop for orphans from before this guard.
    _sentinel_pipe = _attach_lifetime(path, s.fileno())

    # Graceful stop: the default `kill` is SIGTERM, and Ctrl-C is SIGINT;
    # turn both into KeyboardInterrupt so the `finally` unlinks the socket and
    # we exit cleanly. SIGKILL (`kill -9`) and crashes are covered by the
    # sentinel forked above; the stale-socket check at startup is the backstop.
    def _stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    sys.stderr.write(f"copykat listen: watching {path}\n")
    sys.stderr.flush()

    try:
        while True:
            conn, _ = s.accept()
            with conn:
                buf = b""
                while True:
                    try:
                        chunk = conn.recv(65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        _handle(raw, as_json)
                if buf:  # trailing message without a newline
                    _handle(buf, as_json)
    except KeyboardInterrupt:
        pass
    finally:
        s.close()
        try:
            os.unlink(path)
        except OSError:
            pass
    return 0


def _handle(raw: bytes, as_json: bool) -> None:
    text = raw.decode("utf-8", errors="replace").rstrip("\r")
    if not text:
        return
    if as_json:
        _emit(text)  # verbatim JSON line, for programmatic consumers
        return
    # Pretty mode: the viewer sends a JSON envelope {"from","time","text"};
    # unwrap it and print the message body with real newlines instead of the
    # raw escaped JSON. Fall back to the raw line if it isn't our envelope.
    try:
        obj = json.loads(text)
        body = obj.get("text", text)
        ts = time.strftime("%H:%M:%S", time.localtime(obj.get("time")))
    except (ValueError, TypeError):
        body, ts = text, time.strftime("%H:%M:%S")
    _emit(f"[copykat {ts}]\n{body}" if "\n" in body else f"[copykat {ts}] {body}")


def _inbox_alive(path: str) -> bool:
    """True if a watcher is currently listening on *path*."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(path)
        return True
    except OSError:
        return False
