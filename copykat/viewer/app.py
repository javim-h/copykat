"""The Textual viewer: a scrollable list of recorded commands.

Built lazily by :func:`_build_app` so the recorder needs no textual dependency;
all of textual is imported inside the function.
"""

from __future__ import annotations

import json

from ..recorder import request_buffer
from ..sockets import _socket_term_name
from .discovery import _live_watchers, _send_to_watcher, _short_cwd
from .format import _format_message
from .theme import _load_user_theme

__all__ = ["_build_app"]


def _build_app(sock_path: str):
    """Build the Textual viewer (imported lazily so `record` needs no textual)."""
    from textual import work
    from textual.app import App
    from textual.binding import Binding
    from textual.containers import Horizontal, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Footer, ListItem, ListView, Static, TextArea

    class ExpandScreen(ModalScreen):
        BINDINGS = [
            Binding("escape", "close", "Back"),
            Binding("q", "close", "Back"),
            Binding("e", "close", "Back"),
        ]

        def __init__(self, msg: dict) -> None:
            super().__init__()
            self._msg = msg

        def compose(self):
            with VerticalScroll():
                yield Static(_format_message(self._msg, full=True))

        def action_close(self) -> None:
            self.app.pop_screen()

    class ComposeScreen(ModalScreen):
        """Write a git-commit-style message and send it to the current watcher.

        Prefilled like a git commit template: a ``# Add your message`` comment,
        a ``---`` separator, then the pasted recording underneath. Comment
        (``#``) lines are stripped before sending. ctrl+s sends, esc cancels.
        """

        BINDINGS = [
            Binding("ctrl+s", "send", "Send"),
            Binding("escape", "close", "Cancel"),
        ]

        def __init__(self, path: str, who: str, session: str, paste: str) -> None:
            super().__init__()
            self._path = path
            self._who = who
            self._session = session
            self._paste = paste

        def compose(self):
            template = f"# Add your message\n\n---\n{self._paste}"
            with VerticalScroll():
                yield Static(
                    f"to {self._who} / {self._session}   (ctrl+s send, esc cancel)"
                )
                yield TextArea(template, id="compose")

        def on_mount(self) -> None:
            ta = self.query_one("#compose", TextArea)
            ta.focus()
            ta.move_cursor((1, 0))  # the blank line under the comment

        def action_close(self) -> None:
            self.app.pop_screen()

        def action_send(self) -> None:
            text = self.query_one("#compose", TextArea).text
            # git-style: drop comment lines, keep message + separator + paste.
            body = "\n".join(
                ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
            ).strip()
            self.app.pop_screen()
            if not body:
                self.app.notify("empty message, nothing sent", severity="warning")
                return
            if _send_to_watcher(self._path, body):
                self.app.notify(f"sent to {self._who} / {self._session}")
            else:
                self.app.notify("send failed", severity="error")

    class PickScreen(ModalScreen):
        """Choose which ``copykat listen`` inbox (which agent) gets the copy.

        Shown only when more than one inbox is live; selecting one opens the
        :class:`ComposeScreen` aimed at it. esc cancels.
        """

        BINDINGS = [Binding("escape", "close", "Cancel")]

        def __init__(self, targets: list[tuple[str, str, str]], paste: str) -> None:
            super().__init__()
            self._targets = targets
            self._paste = paste

        def compose(self):
            with VerticalScroll():
                yield Static("send to which inbox?   (enter select, esc cancel)")
                yield ListView(
                    *(
                        ListItem(Static(f"{who} / {session}"))
                        for _, who, session in self._targets
                    ),
                    id="inboxes",
                )

        def on_mount(self) -> None:
            lv = self.query_one("#inboxes", ListView)
            lv.focus()
            lv.index = 0

        def on_list_view_selected(self, event) -> None:
            i = self.query_one("#inboxes", ListView).index
            if i is None:
                return
            path, who, session = self._targets[i]
            self.app.pop_screen()
            self.app.push_screen(ComposeScreen(path, who, session, self._paste))

        def action_close(self) -> None:
            self.app.pop_screen()

    class CopyKatApp(App):
        CSS = """
        Screen { background: $background; color: $foreground; }

        /* full viewer (expanded message) */
        ExpandScreen { background: $background; }
        ExpandScreen VerticalScroll { background: $background; color: $foreground; }

        /* title bar */
        #hdr { dock: top; height: 1; background: $background; color: $accent; }
        #hdr-left { width: 1fr; text-align: left; padding-left: 1; text-style: bold; }
        #hdr-mid { width: 1fr; text-align: center; color: $accent; }
        #hdr-right { width: 1fr; text-align: right; padding-right: 1; color: $accent; }

        /* message list + selection */
        /* `background-tint` zeroed: ListView:focus tints bg by $foreground 5%,
           which would lighten the list relative to the rest of the app. */
        #messages { background: $background; background-tint: $background 0%; color: $foreground; margin-top: 1; }
        #messages > ListItem { background: $background; color: $foreground; }
        #messages > ListItem.-highlight,
        #messages:focus > ListItem.-highlight {
            background: $background-hover; color: $foreground-hover; text-style: bold;
        }
        /* marked-for-multi-send rows: accent left border only (text stays normal) */
        #messages > ListItem.-marked {
            border-left: outer $accent;
        }

        /* footer: blend into the message-list background, no panel bar */
        Footer { background: $background; color: $foreground; }
        Footer > FooterKey { background: $background; color: $foreground; }
        Footer > FooterKey .footer-key--key { background: $background; color: $accent; }

        /* notification toasts: notice palette (amber bg, navy text) */
        Toast { background: $notice-bg; color: $notice-fg; border-left: outer $notice-bg; }
        Toast .toast--title { color: $notice-fg; }
        """

        ENABLE_COMMAND_PALETTE = False

        BINDINGS = [
            Binding("e", "expand", "Expand"),
            Binding("space", "toggle_mark", "Mark"),
            Binding("s", "send", "Send"),
            Binding("q", "quit", "Quit"),
        ]

        def get_theme_variable_defaults(self) -> dict[str, str]:
            # `$background-hover` styles the selected row; the palette overrides
            # it via `variables.background-hover`. Default here so the CSS still
            # resolves under the ANSI fallback (no theme file).
            return {
                "background-hover": "#1a2538",
                "foreground-hover": "#ffffff",
                "notice-bg": "#E5C07B",
                "notice-fg": "#000B18",
            }

        def __init__(self) -> None:
            super().__init__()
            self.sock_path = sock_path  # the recorder on this tty, fixed for life
            self._rendered: dict[int, ListItem] = {}  # seq to list row
            self._by_seq: dict[int, dict] = {}  # seq to message
            self._marked: set[int] = set()  # seqs selected for multi-send

        def compose(self):
            with Horizontal(id="hdr"):
                yield Static("copykat", id="hdr-left")
                yield Static(_socket_term_name(self.sock_path), id="hdr-mid")
                yield Static(_short_cwd(), id="hdr-right")
            yield ListView(id="messages")
            yield Footer()

        def on_mount(self) -> None:
            # The installer always drops a palette at ~/.copykat/theme.yaml, so this
            # normally applies the copykat theme. If it's somehow absent we keep
            # Textual's built-in dark theme rather than the terminal's ANSI palette
            # (which would tint the accent green).
            user_theme = _load_user_theme()
            if user_theme is not None:
                self.register_theme(user_theme)
                self.theme = user_theme.name
            self.poll()
            self.set_interval(0.5, self.poll)

        @work(exclusive=True, thread=True)
        def poll(self) -> None:
            try:
                payload = request_buffer(self.sock_path)
            except (OSError, json.JSONDecodeError):
                payload = None
            self.call_from_thread(self._apply, payload)

        def _apply(self, payload: dict | None) -> None:
            if payload is None:
                return  # transient miss; keep showing what we have
            msgs = payload.get("messages", [])
            self._by_seq = {m["seq"]: m for m in msgs}
            lv = self.query_one("#messages", ListView)
            # Follow the latest message unless the user has scrolled up to browse.
            at_tail = lv.index is None or lv.index >= len(lv.children) - 1
            for seq in [s for s in self._rendered if s not in self._by_seq]:
                self._rendered.pop(seq).remove()  # evicted from the ring buffer
                self._marked.discard(seq)  # ponytail: stale marks age out with the row
            for m in msgs:
                seq = m["seq"]
                if seq in self._rendered:
                    self._rendered[seq].query_one(Static).update(_format_message(m))
                else:
                    item = ListItem(Static(_format_message(m)))
                    self._rendered[seq] = item
                    lv.append(item)
                    if seq in self._marked:
                        item.add_class("-marked")
            if at_tail and len(lv.children):
                lv.index = len(lv.children) - 1

        def _highlighted_msg(self) -> dict | None:
            lv = self.query_one("#messages", ListView)
            item = lv.highlighted_child
            if item is None:
                return None
            seq = next((s for s, it in self._rendered.items() if it is item), None)
            return self._by_seq.get(seq) if seq is not None else None

        def _seq_for_item(self, item) -> int | None:
            return next((s for s, it in self._rendered.items() if it is item), None)

        def action_expand(self) -> None:
            msg = self._highlighted_msg()
            if msg is not None:
                self.push_screen(ExpandScreen(msg))

        def action_toggle_mark(self) -> None:
            item = self.query_one("#messages", ListView).highlighted_child
            if item is None:
                return
            seq = self._seq_for_item(item)
            if seq is None:
                return
            if seq in self._marked:
                self._marked.discard(seq)
                item.remove_class("-marked")
            else:
                self._marked.add(seq)
                item.add_class("-marked")

        def action_send(self) -> None:
            targets = _live_watchers()
            if not targets:
                self.notify("no copykat listen inbox running", severity="warning")
                return
            # Marked messages take priority; otherwise send the highlighted one.
            seqs = sorted(self._marked) if self._marked else []
            msgs = [self._by_seq[s] for s in seqs if s in self._by_seq]
            if not msgs:
                m = self._highlighted_msg()
                if m is None:
                    self.notify("nothing to send", severity="warning")
                    return
                msgs = [m]
            # Join with a blank line between commands so the paste reads as
            # several recordings, not one merged blob.
            paste = "\n\n".join(_format_message(m, full=True) for m in msgs)
            # One inbox: go straight to compose. Several: let the user pick
            # which agent gets the copy.
            if len(targets) == 1:
                path, who, session = targets[0]
                self.push_screen(ComposeScreen(path, who, session, paste))
            else:
                self.push_screen(PickScreen(targets, paste))

    return CopyKatApp()
