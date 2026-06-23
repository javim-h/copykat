"""Parse a session's *outbound* PTY stream into a ring buffer of messages.

Raw PTY bytes are incrementally UTF-8 decoded, split into logical lines, and
ANSI/OSC escapes are stripped so each :class:`Message` carries clean text. The
output is split into one :class:`Message` per command: ``copykat record`` injects
OSC 133 semantic-prompt markers into the shell it launches, so each shell prompt
marks where one command's output ends and the next begins. The last N messages
are kept in an in-memory ring buffer.
"""

from __future__ import annotations

import codecs
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field

__all__ = ["Message", "PtyRecorder", "strip_ansi"]

# Byte budget for the recorder's live memory: the finalized ring buffer *plus*
# the in-progress command's output. Enough headroom for a big compile/log dump
# across the last `maxlen` commands, negligible on any modern machine. Without
# it a single never-ending command (a full-screen TUI that never returns to a
# prompt) would grow `self._cur` without bound.
# ponytail: fixed, no knob; raise here if a real session starves on output.
_DEFAULT_MAXSIZE = 16 * 1024 * 1024

# Cap on the partial line being assembled (`self._line`). A full-screen TUI
# (top, vim, a nested viewer) repaints with cursor-positioning escapes and emits
# almost no newlines, so without a cap `self._line` grows unbounded and every
# feed re-scans the whole string for a newline (O(n^2) CPU). Once the partial
# line reaches this we flush it as one line, keeping the rescan bounded. Kept
# small (and separate from `maxsize`) precisely so that rescan stays cheap.
_MAX_LINE = 64 * 1024


def _msg_size(m: "Message") -> int:
    """Approximate bytes a finalized :class:`Message` occupies in the buffer."""
    return len(m.command) + sum(len(line) for line in m.output)

# CSI sequences (e.g. colours, cursor moves) and lone two-byte escapes.
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
# OSC sequences (e.g. window-title sets), terminated by BEL or ST.
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# Stray control bytes worth dropping from the parsed text (keep \t).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Line boundaries: in raw tty mode Enter is a bare \r, output uses \r\n or \n.
_NL_RE = re.compile(r"\r\n|\r|\n")

# OSC 133 semantic-prompt markers (shell integration), e.g. ESC ] 133 ; A BEL.
# `copykat record` injects the matching prompt into the shell it spawns; the
# recorder splits the outbound stream on these (see :class:`PtyRecorder`).
_OSC133_RE = re.compile(r"\x1b\]133;([A-D])((?:;[^\x07\x1b]*)?)(?:\x07|\x1b\\)")

# `copykat viewer` brackets every frame it writes with these private OSC markers.
# Real terminals ignore unknown OSC strings (consumed up to BEL), so they're
# invisible on screen; the recorder watches for them and drops everything in
# between, so the viewer's own output never feeds back into the ring buffer.
_VIEW_TAG = "copykat:viewer"
_VIEW_BEGIN = f"\x1b]666;{_VIEW_TAG}:begin\x07"
_VIEW_END = f"\x1b]666;{_VIEW_TAG}:end\x07"


def _partial_tail(data: str, marker: str) -> int:
    """Length of the longest suffix of *data* that is a prefix of *marker*.

    Lets the parser hold back bytes that might be the start of a marker split
    across two reads, instead of emitting them prematurely.
    """
    for k in range(min(len(data), len(marker) - 1), 0, -1):
        if marker.startswith(data[-k:]):
            return k
    return 0


def _osc_tail(data: str) -> int:
    """Length of a trailing, not-yet-terminated OSC 133 marker to hold back.

    An ``ESC ] 133 ; ... BEL`` marker can be split across two reads. If *data*
    ends part-way through one, return how many trailing bytes to carry over so
    the fragment isn't emitted (and mis-parsed) before the rest arrives.
    """
    lead = "\x1b]133;"
    # A partial lead-in sitting right at the end (e.g. data ends "\x1b]1").
    for k in range(min(len(data), len(lead) - 1), 0, -1):
        if data.endswith(lead[:k]):
            return k
    # A full lead-in present but no terminator yet (e.g. "\x1b]133;D;0").
    i = data.rfind(lead)
    if i != -1:
        tail = data[i:]
        if "\x07" not in tail and "\x1b\\" not in tail[len(lead):]:
            return len(tail)
    return 0


def strip_ansi(text: str) -> str:
    """Return *text* with ANSI/OSC escapes and stray control bytes removed."""
    text = _OSC_RE.sub("", text)
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r", "")
    return _CTRL_RE.sub("", text)


@dataclass
class Message:
    """One command and all of its output, grouped together.

    A message spans from one shell prompt to the next, so e.g. ``ll`` is a
    single message whose ``output`` holds every row it printed, not one message
    per row. ``command`` is the command echoed after the prompt ("" for output
    printed before the first prompt, e.g. a shell banner).
    """

    seq: int
    time: float
    command: str  # the command echoed after the prompt ("" before the first)
    output: list[str] = field(default_factory=list)  # parsed output lines

    def copy(self) -> "Message":
        return Message(self.seq, self.time, self.command, list(self.output))

    def __str__(self) -> str:
        head = self.command or "(session start)"
        lines = [f"[{self.seq:>3} @ {self.time:.3f}] $ {head}"]
        lines += [f"        {line}" for line in self.output]
        return "\n".join(lines)


_SENTINEL = object()


class PtyRecorder:
    """Parse a session's *outbound* stream into a ring buffer of messages.

    Feed raw PTY output bytes with :meth:`feed`. Messages are split on the shell
    prompt: ``copykat record`` injects OSC 133 semantic-prompt markers into the
    shell it spawns (see :func:`_with_shell_integration`), so every prompt
    brackets one command and its output. Nothing you *type* is read (only what
    the terminal prints), so secrets entered at a no-echo prompt (passwords)
    never enter the buffer.

    OSC 133 markers, emitted by the injected prompt:
      * ``A``        prompt start:  boundary, finalize message, begin a new one
      * ``B``        prompt end:    the echoed command text starts here
      * ``C``        pre-exec:      the command's output starts here
      * ``D;<exit>`` finished:      command done (exit status available)
    """

    def __init__(self, maxlen: int = 10, maxsize: int = _DEFAULT_MAXSIZE) -> None:
        self.maxlen = maxlen
        self.maxsize = maxsize  # fixed byte cap; 0 disables size-based eviction
        self._buf: deque[Message] = deque()
        self._size = 0           # bytes held in self._buf (finalized messages)
        self._cur: Message | None = None  # command currently collecting output
        self._cur_size = 0       # bytes held in self._cur.output (in-progress)
        self._lock = threading.Lock()
        self._q: "queue.Queue[object]" = queue.Queue()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._muted = False     # inside a copykat:viewer marker region
        self._vcarry = ""       # held-back possible split viewer marker
        self._pcarry = ""       # held-back possible split OSC sequence
        self._phase = "output"  # output | prompt | command
        self._line = ""         # output line being assembled
        self._cmd = ""          # command text being assembled (B to C)
        self._seq = 0
        self._worker: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------
    def start(self) -> "PtyRecorder":
        if self._worker is None:
            self._worker = threading.Thread(
                target=self._run, name="copykat-recorder", daemon=True
            )
            self._worker.start()
        return self

    def feed(self, data: bytes, source: str = "out") -> None:
        """Hand raw *outbound* session bytes to the recorder (non-blocking).

        ``source`` is accepted for backwards compatibility but ignored: only the
        terminal's output is recorded now.
        """
        self._q.put(data)

    def stop(self, timeout: float | None = 2.0) -> None:
        """Flush any trailing partial lines and stop the worker thread."""
        self._q.put(_SENTINEL)
        if self._worker is not None:
            self._worker.join(timeout)
            self._worker = None

    # -- reading the buffer ----------------------------------------------
    def messages(self) -> list[Message]:
        """Return a snapshot of the last ``maxlen`` commands (incl. in-progress)."""
        with self._lock:
            msgs = list(self._buf)
            if self._cur is not None:
                msgs.append(self._cur.copy())
            return msgs[-self.maxlen:]

    # -- worker internals -------------------------------------------------
    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is _SENTINEL:
                self._process(self._decoder.decode(b"", final=True), flush=True)
                with self._lock:  # finalize the last command's output
                    self._finalize_locked()
                break
            try:
                self._process(self._decoder.decode(item), flush=False)  # type: ignore[arg-type]
            except Exception:
                pass  # never let a parse error kill the recorder thread

    def _process(self, text: str, flush: bool) -> None:
        self._scan133(self._descan(text, flush), flush)

    def _descan(self, text: str, flush: bool) -> str:
        """Strip copykat:viewer markers and drop text inside marked regions."""
        data = self._vcarry + text
        self._vcarry = ""
        out: list[str] = []
        while data:
            if self._muted:
                idx = data.find(_VIEW_END)
                if idx == -1:
                    keep = 0 if flush else _partial_tail(data, _VIEW_END)
                    self._vcarry = data[len(data) - keep:] if keep else ""
                    break  # rest is muted, discard it
                data = data[idx + len(_VIEW_END):]
                self._muted = False
            else:
                idx = data.find(_VIEW_BEGIN)
                if idx == -1:
                    keep = 0 if flush else _partial_tail(data, _VIEW_BEGIN)
                    out.append(data[: len(data) - keep] if keep else data)
                    self._vcarry = data[len(data) - keep:] if keep else ""
                    break
                out.append(data[:idx])
                data = data[idx + len(_VIEW_BEGIN):]
                self._muted = True
        return "".join(out)

    def _scan133(self, text: str, flush: bool) -> None:
        """Route text by OSC 133 prompt phase, splitting on each new prompt."""
        data = self._pcarry + text
        self._pcarry = ""
        while data:
            m = _OSC133_RE.search(data)
            if not m:
                # Hold back a trailing partial marker split across two reads.
                keep = 0 if flush else _osc_tail(data)
                self._emit(data[: len(data) - keep] if keep else data, flush)
                self._pcarry = data[len(data) - keep:] if keep else ""
                return
            self._emit(data[: m.start()], flush=False)
            self._mark(m.group(1), m.group(2) or "")
            data = data[m.end():]

    def _mark(self, kind: str, arg: str) -> None:
        if kind == "A":            # new prompt: message boundary
            self._flush_line()
            with self._lock:
                self._finalize_locked()
                self._seq += 1
                self._cur = Message(self._seq, time.time(), command="")
            self._phase = "prompt"
            self._cmd = ""
        elif kind == "B":          # prompt drawn: the echoed command follows
            self._phase = "command"
        elif kind == "C":          # command runs: its output begins
            cmd = self._cmd.strip()
            with self._lock:
                if self._cur is not None:
                    self._cur.command = cmd
            self._phase = "output"
        elif kind == "D":          # command finished (arg is ";<exit>")
            self._phase = "output"

    def _emit(self, text: str, flush: bool = False) -> None:
        if not text:
            return
        if self._phase == "prompt":
            return  # the prompt's own drawing, discard it
        if self._phase == "command":
            self._cmd += strip_ansi(text)
            return
        self._line += text
        while True:
            m = _NL_RE.search(self._line)
            if not m:
                break
            # A lone trailing '\r' may be the first half of a split '\r\n'.
            if m.group() == "\r" and m.end() == len(self._line) and not flush:
                break
            self._append_output(self._line[: m.start()])
            self._line = self._line[m.end():]
        # A newline-sparse stream (a full-screen TUI repainting in place) would
        # let `self._line` grow without bound and make each feed re-scan the
        # whole string for a newline (O(n^2)). Cap it: once the partial line
        # reaches `_MAX_LINE` without a newline, flush it as one line so the
        # rescan stays bounded. `_flush_line` feeds it into `self._cur`, where
        # the `maxsize` budget then bounds the overall memory.
        if flush or len(self._line) >= _MAX_LINE:
            self._flush_line()

    def _flush_line(self) -> None:
        if self._line:
            self._append_output(self._line)
            self._line = ""

    def _append_output(self, line: str) -> None:
        text = strip_ansi(line)
        if not text:  # drop lines that were pure escape sequences / blank
            return
        with self._lock:
            if self._cur is None:  # output before any prompt: session start
                self._seq += 1
                self._cur = Message(self._seq, time.time(), command="")
                self._cur_size = 0
            self._cur.output.append(text)
            self._cur_size += len(text)
            self._trim_locked()

    def _finalize_locked(self) -> None:
        """Move the in-progress command into the ring buffer. Caller holds lock."""
        if self._cur is not None:
            self._buf.append(self._cur)
            self._size += _msg_size(self._cur)
            self._cur = None
            self._cur_size = 0
            self._trim_locked()

    def _trim_locked(self) -> None:
        """Keep live bytes under ``maxsize`` and the buffer under ``maxlen``.

        The size budget covers *all* live bytes (the finalized ring buffer plus
        the in-progress command), so a single never-ending command (a full-screen
        TUI that never returns to a prompt) can't grow memory without bound.
        Whole finalized messages are evicted first (oldest history); only if the
        in-progress command alone still blows the budget do we drop its oldest
        output lines. Caller holds the lock.
        """
        while self._buf:
            if len(self._buf) > self.maxlen:
                ev = self._buf.popleft()
            elif self.maxsize and self._size + self._cur_size > self.maxsize:
                ev = self._buf.popleft()
            else:
                break
            self._size -= _msg_size(ev)
        if self._size < 0:
            self._size = 0
        # In-progress command over budget on its own: drop oldest output lines
        # down to a low-water mark, so we re-trim once per ~maxsize/8 bytes
        # rather than on every line (amortized O(1) per line, not O(n^2)).
        if self.maxsize and self._cur is not None and \
                self._size + self._cur_size > self.maxsize:
            out = self._cur.output
            low = self.maxsize - self.maxsize // 8
            i = 0
            while i < len(out) and self._size + self._cur_size > low:
                self._cur_size -= len(out[i])
                i += 1
            del out[:i]
            if self._cur_size < 0:
                self._cur_size = 0
