"""``copykat stop``: stop a running recorder (ends its shell session)."""

from __future__ import annotations

import sys

from ..recorder.server import _request
from ..sockets import _resolve_socket

__all__ = ["_cmd_stop"]

_STOP_USAGE = """\
usage: copykat stop [-s PATH]

Stop a running recorder (ends its shell session). With no -s, targets the only
running recorder.

options:
  -s, --socket P   recorder socket to stop
  -h, --help       show this help and exit
"""


def _cmd_stop(args: list[str]) -> int:
    sock_path: str | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            sys.stdout.write(_STOP_USAGE)
            return 0
        if a in ("-s", "--socket"):
            if i + 1 >= len(args):
                sys.stderr.write("copykat: --socket requires a value\n")
                return 2
            sock_path = args[i + 1]
            i += 2
        else:
            sys.stderr.write(f"copykat: unknown option {a!r}\n{_STOP_USAGE}")
            return 2

    resolved, err = _resolve_socket(sock_path)
    if err:
        sys.stderr.write(err + "\n")
        return 1
    try:
        _request(resolved, b"stop\n")  # type: ignore[arg-type]
    except OSError:
        sys.stderr.write(f"copykat: no recorder running on {resolved}\n")
        return 1
    sys.stderr.write(f"copykat: stopped recorder on {resolved}\n")
    return 0
