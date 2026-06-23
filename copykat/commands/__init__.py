"""Subcommand handlers for the ``copykat`` CLI (record, viewer, listen, stop)."""

from __future__ import annotations

from .listen import _cmd_listen
from .record import _cmd_record
from .stop import _cmd_stop
from .viewer import _cmd_viewer

__all__ = ["_cmd_record", "_cmd_viewer", "_cmd_listen", "_cmd_stop"]
