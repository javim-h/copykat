"""``copykat record``: record a terminal session to an in-memory ring buffer."""

from __future__ import annotations

import os
import pty
import sys

from ..recorder import PtyRecorder, RecorderServer
from ..recorder.icon import CAT_ICON
from ..recorder.proxy import _run_interactive, _run_piped, _with_shell_integration
from ..recorder.server import _recorder_alive
from ..sockets import _SOCKET_ENV, _default_socket

__all__ = ["_cmd_record"]

_RECORD_USAGE = """\
usage: copykat record [-n N] [-s PATH] [-q] [-- ] [command [args...]]

Record a terminal session's I/O to an in-memory ring buffer and serve it to
viewers over a per-terminal Unix socket.

options:
  -n, --maxlen N   keep the last N commands (default: 10)
  -s, --socket P   socket to serve on (default: /tmp/copykat-<terminal>.sock)
  -q, --quiet      don't print the recorded buffer on exit
  -h, --help       show this help and exit

With no command, your $SHELL (or /bin/bash) is launched.
"""


def _cmd_record(args: list[str]) -> int:
    maxlen, dump, sock_path = 10, True, None
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a in ("-h", "--help"):
            sys.stdout.write(_RECORD_USAGE)
            return 0
        if a in ("-q", "--quiet"):
            dump = False
            i += 1
        elif a in ("-n", "--maxlen"):
            if i + 1 >= len(args):
                sys.stderr.write("copykat: --maxlen requires a value\n")
                return 2
            try:
                maxlen = int(args[i + 1])
            except ValueError:
                sys.stderr.write(f"copykat: --maxlen needs an integer, got {args[i + 1]!r}\n")
                return 2
            if maxlen < 1:
                sys.stderr.write("copykat: --maxlen must be at least 1\n")
                return 2
            i += 2
        elif a in ("-s", "--socket"):
            if i + 1 >= len(args):
                sys.stderr.write("copykat: --socket requires a value\n")
                return 2
            sock_path = args[i + 1]
            i += 2
        else:
            sys.stderr.write(f"copykat: unknown option {a!r}\n{_RECORD_USAGE}")
            return 2

    explicit_cmd = args[i:]
    cmd = explicit_cmd or [os.environ.get("SHELL", "/bin/bash")]

    # Refuse to nest: if we're already inside a recorded session (record exports
    # $COPYKAT_SOCKET into the shell it spawns) and that recorder still answers,
    # starting another would silently stack a second, empty recorder on top.
    inner = os.environ.get(_SOCKET_ENV)
    if inner and _recorder_alive(inner):
        sys.stderr.write(
            f"copykat: already inside a recorded session ({inner}).\n"
            f"        run `copykat viewer` to attach, or `copykat stop` to end it.\n"
        )
        return 1

    # Open the pty now so we can name the socket after the *inner* pty the shell
    # will run on. A viewer launched inside the recorded session sees that pty as
    # its own tty, so naming the socket after it lets the viewer attach by its
    # own terminal id; without it the viewer would look for the outer terminal.
    master, slave = pty.openpty()
    sock_path = sock_path or _default_socket(slave)

    # One recording per terminal: refuse if a recorder already answers here.
    if _recorder_alive(sock_path):
        os.close(master)
        os.close(slave)
        sys.stderr.write(
            f"copykat: already recording this terminal ({sock_path}).\n"
            f"        stop it with `copykat stop` first.\n"
        )
        return 1

    # Let `copykat stop` invoked inside the recorded shell find us.
    os.environ[_SOCKET_ENV] = sock_path

    rec = PtyRecorder(maxlen=maxlen).start()
    stop_r, stop_w = os.pipe()
    server = RecorderServer(
        rec, sock_path, on_stop=lambda: os.write(stop_w, b"x")
    ).start()
    sys.stderr.write(f"\033[1;36m{CAT_ICON} copykat recording\033[0m\n")
    # Splitting output into commands needs the shell to emit OSC 133 prompt
    # markers; inject them when we launch the default shell ourselves. A command
    # the user named explicitly runs as-is (no integration, a single message).
    rcfile = None
    if not explicit_cmd:
        cmd, rcfile = _with_shell_integration(cmd)
    try:
        run = _run_interactive if sys.stdin.isatty() else _run_piped
        status = run(cmd, rec, master, slave, stop_fd=stop_r)
    finally:
        server.stop()
        rec.stop()
        os.close(stop_r)
        os.close(stop_w)
        if rcfile is not None:
            try:
                os.unlink(rcfile)
            except OSError:
                pass

    if dump:
        msgs = rec.messages()
        sys.stderr.write(f"\n--- copykat: last {len(msgs)} parsed message(s) ---\n")
        for m in msgs:
            sys.stderr.write(str(m) + "\n")

    return status or 0
