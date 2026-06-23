"""Render a recorded message for the viewer's list and expand views."""

from __future__ import annotations

__all__ = ["_format_message"]

_CROP_LINES = 8  # output lines shown per command in the list before cropping


def _format_message(msg: dict, full: bool = False) -> str:
    head = msg["command"] or "(session start)"
    out = msg["output"]
    if not full and len(out) > _CROP_LINES:
        extra = len(out) - _CROP_LINES
        body = out[:_CROP_LINES] + [f"... {extra} more line(s); press e to expand"]
    else:
        body = out
    text = f"$ {head}"
    return text + ("\n" + "\n".join(body) if body else "")
