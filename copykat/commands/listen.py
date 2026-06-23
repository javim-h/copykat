"""``copykat listen``: a Unix-socket inbox an AI agent monitors for messages.

The inverse of ``record``/``viewer``: where the viewer sends, this listens and
prints every message pushed at it (see :mod:`copykat.watcher`). An AI agent runs
it in the background; the copykat app pushes the highlighted recording with `s`.
"""

from __future__ import annotations

import argparse

from ..watcher import _socket_path, watch

__all__ = ["_cmd_listen"]


def _cmd_listen(args: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="copykat listen",
        description="Unix-socket inbox an AI agent can monitor for copykat messages.",
    )
    p.add_argument(
        "--who", default="claude-code",
        help="agent identity in the socket key (default: claude-code)",
    )
    p.add_argument(
        "--session", required=True,
        help="session title in the socket key",
    )
    p.add_argument(
        "-d", "--dir", default=None,
        help="directory for the socket (default: system temp dir)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="print received messages verbatim (no timestamp prefix)",
    )
    ns = p.parse_args(args)

    path = _socket_path(ns.who, ns.session, ns.dir)
    return watch(path, ns.json)
