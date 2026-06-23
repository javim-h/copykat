"""``copykat viewer``: the Textual viewer over a recorder's buffer."""

from __future__ import annotations

import sys

from ..recorder.parser import _VIEW_BEGIN, _VIEW_END
from ..recorder.server import _recorder_alive
from ..sockets import _default_socket
from ..viewer import _build_app

__all__ = ["_cmd_viewer"]

_VIEWER_USAGE = """\
usage: copykat viewer

Textual viewer: a scrollable list of recorded commands. Long output is cropped
in the list; highlight a command and press `e` to expand it full-screen.

Always attached to this terminal's own recorder (/tmp/copykat-<this-tty>.sock).
Press `s` to send the highlighted recording to a `copykat listen` inbox; when
more than one is running, you choose which agent gets the copy.

options:
  -h, --help       show this help and exit

Keys: up/down move, e expand, space mark, s send to copykat listen, q quit
"""


def _cmd_viewer(args: list[str]) -> int:
    for a in args:
        if a in ("-h", "--help"):
            sys.stdout.write(_VIEWER_USAGE)
            return 0
        sys.stderr.write(f"copykat: unknown option {a!r}\n{_VIEWER_USAGE}")
        return 2

    # The viewer is fixed to the current terminal's own recorder (the socket
    # named after this tty), so there's no -s launch override and no
    # $COPYKAT_SOCKET lookup.
    resolved = _default_socket()
    if not _recorder_alive(resolved):
        sys.stderr.write(
            f"copykat: no recorder on this terminal ({resolved}).\n"
            f"        run `copykat record` here first.\n"
        )
        return 1

    try:
        app = _build_app(resolved)
    except ModuleNotFoundError:
        sys.stderr.write("copykat: textual is required for `viewer` (pip install textual)\n")
        return 1

    # If this viewer runs inside a recorded shell, bracket all of Textual's
    # output with the markers so the recorder mutes it (no feedback loop).
    sys.stdout.write(_VIEW_BEGIN)
    sys.stdout.flush()
    try:
        app.run()
    finally:
        sys.stdout.write(_VIEW_END)
        sys.stdout.flush()
    return 0
