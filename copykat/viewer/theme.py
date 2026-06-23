"""User color-palette for the viewer (~/.copykat/theme.yaml).

The installer ships a ``theme.yaml`` into ``~/.copykat``; this module reads it and
builds a Textual ``Theme``. The file uses a small nested schema rather than raw
Textual fields::

    background:
      color: "#000B18"   # screen + message-list background
      hover: "#1a2538"   # selected-row background (lighter tone of color)
    foreground:
      color: "#dddddd"   # body text
      hover: "#ffffff"   # selected-row text
      accent: "#fd0059"  # title bar text + toasts
    notice:
      color: "#E5C07B"   # toast background
      text: "#000B18"    # toast text
"""

from __future__ import annotations

import os

__all__ = ["_load_user_theme"]


def _user_theme_path() -> str:
    """Path of the user color-palette file: ~/.copykat/theme.yaml."""
    return os.path.join(os.path.expanduser("~"), ".copykat", "theme.yaml")


def _load_user_theme():
    """Build a Textual ``Theme`` from ~/.copykat/theme.yaml, or ``None``.

    The palette only specifies a ``background`` (``color`` + ``hover``) and a
    ``foreground`` (``color`` + ``accent``); ``background.color`` and
    ``foreground.accent`` are required, the rest Textual derives. Any problem
    (missing file, no PyYAML, bad YAML, missing required key) returns ``None`` so
    the viewer keeps Textual's built-in dark theme; theming never breaks it.
    """
    path = _user_theme_path()
    if not os.path.exists(path):
        return None
    try:
        import yaml
    except ModuleNotFoundError:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    bg = data.get("background") or {}
    fg = data.get("foreground") or {}
    notice = data.get("notice") or {}
    if not isinstance(bg, dict) or not isinstance(fg, dict):
        return None
    background, accent = bg.get("color"), fg.get("accent")
    if not background or not accent:
        return None

    # Map the nested schema onto Textual's flat Theme. `accent` doubles as
    # `primary` (Theme requires it); `background.hover` styles the selected row
    # via the `$background-hover` CSS variable. `notice.color`/`text` drive the
    # toast background/text via `$notice-bg`/`$notice-fg`.
    kwargs = {
        "name": "copykat",
        "dark": True,
        "background": background,
        "accent": accent,
        "primary": accent,
    }
    if fg.get("color"):
        kwargs["foreground"] = fg["color"]
    variables: dict[str, str] = {}
    if bg.get("hover"):
        variables["background-hover"] = bg["hover"]
    if fg.get("hover"):
        variables["foreground-hover"] = fg["hover"]
    if isinstance(notice, dict):
        if notice.get("color"):
            variables["notice-bg"] = notice["color"]
        if notice.get("text"):
            variables["notice-fg"] = notice["text"]
    if variables:
        kwargs["variables"] = variables

    from textual.theme import Theme

    try:
        return Theme(**kwargs)
    except (TypeError, ValueError):
        return None
