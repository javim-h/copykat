"""The ``copykat`` CLI dispatcher: record, viewer, listen, stop."""

from __future__ import annotations

import sys

from .commands import _cmd_listen, _cmd_record, _cmd_stop, _cmd_viewer

__all__ = ["main"]


_USAGE = (
    "usage: copykat [viewer] | copykat {record,listen,stop} [options] ...\n"
    "\n"
    "With no subcommand, copykat launches the viewer (the default).\n"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Bare `copykat` launches the viewer; it's the default command.
    if not args:
        return _cmd_viewer([])

    if args[0] in ("-h", "--help"):
        sys.stdout.write(_USAGE)
        return 0

    sub, rest = args[0], args[1:]
    if sub == "record":
        return _cmd_record(rest)
    if sub == "viewer":
        return _cmd_viewer(rest)
    if sub == "listen":
        return _cmd_listen(rest)
    if sub == "stop":
        return _cmd_stop(rest)

    sys.stderr.write(
        f"copykat: unknown command {sub!r} "
        f"(try 'record', 'viewer', 'listen' or 'stop')\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
