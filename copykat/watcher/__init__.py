"""The watcher layer: a Unix-socket inbox that prints messages pushed at it."""

from __future__ import annotations

from .inbox import _socket_path, watch

__all__ = ["watch", "_socket_path"]
