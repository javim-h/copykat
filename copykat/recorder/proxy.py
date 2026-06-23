"""PTY proxy: spawn a command under a pseudo-terminal and tap its output.

``copykat record`` spawns a command (default: your ``$SHELL``) under a pty and
records the session's *outbound* stream (everything the terminal prints) on a
background thread. Your keystrokes are forwarded to the shell but never
recorded, so a password typed at a no-echo prompt can't leak into the buffer.
"""

from __future__ import annotations

import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import tty

from .parser import PtyRecorder

__all__ = ["_run_interactive", "_run_piped", "_with_shell_integration"]


def _get_winsize(fd: int) -> bytes:
    """Return the packed (rows, cols, xpix, ypix) winsize of *fd*."""
    try:
        return fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
    except OSError:
        return struct.pack("HHHH", 24, 80, 0, 0)


def _announce_cwd(pid: int) -> None:
    """Tell the terminal the child's cwd via OSC 7.

    ``exec copykat record`` replaces the process the terminal watches, so
    without an explicit cwd report the terminal keeps pointing at the wrong
    directory and new tabs open there. Called once at startup. Linux-only
    (``/proc``); silently no-ops elsewhere.

    Uses ``localhost`` as the OSC 7 hostname: VTE/konsole/etc. all treat it
    as local by definition, while the real hostname sometimes fails their
    local-address check (e.g. long DHCP hostnames that don't resolve).
    """
    try:
        target = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return
    try:
        os.chdir(target)
    except OSError:
        pass
    try:
        os.write(1, f"\x1b]7;file://localhost{target}\x07".encode())
    except OSError:
        pass


def _run_interactive(
    cmd: list[str], rec: PtyRecorder, master: int, slave: int, stop_fd: int = -1
) -> int:
    """Proxy a real tty: forward stdin, mirror output, tap both directions.

    The caller owns the *master*/*slave* pty pair (it names the recorder's
    socket after the slave's tty); we run the shell on it. Unlike ``pty.spawn``,
    owning the master fd lets us size the child PTY to the real terminal and
    propagate SIGWINCH; without that the child assumes a default size and its
    line wrapping/redraw corrupts on scrollback or resize.

    A readable *stop_fd* (a pipe written by ``copykat stop``) ends the session.
    """
    # Match the child PTY to the real terminal *before* the shell starts.
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, _get_winsize(1))
    except OSError:
        pass

    pid = os.fork()
    if pid == 0:  # child: become session leader on the slave, then exec
        os.setsid()
        try:
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        except OSError:
            pass
        for dst in (0, 1, 2):
            os.dup2(slave, dst)
        if slave > 2:
            os.close(slave)
        os.close(master)
        try:
            os.execvp(cmd[0], cmd)
        finally:
            os._exit(127)

    os.close(slave)

    old_attr = None
    try:
        old_attr = termios.tcgetattr(0)
        tty.setraw(0)
    except termios.error:
        pass

    def on_winch(*_):
        try:
            fcntl.ioctl(master, termios.TIOCSWINSZ, _get_winsize(1))
        except OSError:
            pass

    old_winch = signal.signal(signal.SIGWINCH, on_winch)

    fds = [master, 0] + ([stop_fd] if stop_fd >= 0 else [])
    stopped = False
    # Tell the terminal where this tab is. ``exec copykat record`` replaces
    # the process the terminal watches, so without this the terminal can't
    # follow the cwd and new tabs open in the wrong directory.
    _announce_cwd(pid)
    try:
        while True:
            rlist, _, _ = select.select(fds, [], [])
            if stop_fd >= 0 and stop_fd in rlist:
                stopped = True
                break  # `copykat stop` asked us to end the session
            if master in rlist:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                rec.feed(data, "out")
                os.write(1, data)
            if 0 in rlist:
                try:
                    data = os.read(0, 65536)
                except OSError:
                    data = b""
                if not data:
                    fds.remove(0)  # our stdin closed; keep draining output
                else:
                    os.write(master, data)  # forward keystrokes; never recorded
    finally:
        signal.signal(signal.SIGWINCH, old_winch)
        if old_attr is not None:
            termios.tcsetattr(0, termios.TCSADRAIN, old_attr)
        if sys.stdout.isatty():
            # Undo modes the recorded shell may have left on (bracketed paste,
            # hidden cursor, lingering SGR) so the terminal is usable on exit.
            os.write(1, b"\x1b[?2004l\x1b[?25h\x1b[0m\r")
        os.close(master)

    if stopped:  # closing the master hangs up the pty; nudge the shell too
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def _run_piped(
    cmd: list[str], rec: PtyRecorder, master: int, slave: int, stop_fd: int = -1
) -> int:
    """Fallback when stdin isn't a tty: still proxy both directions.

    The caller owns the *master*/*slave* pty pair (see :func:`_run_interactive`).
    """
    proc = subprocess.Popen(
        cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True
    )
    os.close(slave)
    fds = [master, 0] + ([stop_fd] if stop_fd >= 0 else [])
    try:
        while True:
            rlist, _, _ = select.select(fds, [], [])
            if stop_fd >= 0 and stop_fd in rlist:
                proc.terminate()
                break
            if master in rlist:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    data = b""
                if not data:
                    break
                rec.feed(data, "out")
                os.write(1, data)
            if 0 in rlist:
                data = os.read(0, 4096)
                if not data:
                    fds = [f for f in fds if f != 0]  # stdin closed; drain output
                else:
                    os.write(master, data)  # forward keystrokes; never recorded
    finally:
        os.close(master)
    return proc.wait()


# Bash startup snippet (sourced via --rcfile) that brackets the prompt with
# OSC 133 markers, so the recorder can split output into command+output messages
# without reading keystrokes. It loads the user's own config first, so their
# prompt/aliases are unchanged; we only wrap PS1 and add a PROMPT_COMMAND hook.
# The hook also re-emits OSC 7 (cwd) on every prompt: our shell is interactive
# but not a login shell, so it never sources /etc/profile.d/vte*.sh, which is
# what normally keeps the terminal's cwd in sync (so new tabs inherit the dir).
_BASH_INTEGRATION = r"""
if [ -f /etc/bash.bashrc ]; then . /etc/bash.bashrc; fi
if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi
__copykat_d() { local e=$?; printf '\033]133;D;%s\007' "$e"; printf '\033]7;file://localhost%s\007' "$PWD"; }
case ";${PROMPT_COMMAND};" in
  *";__copykat_d;"*) ;;
  *) PROMPT_COMMAND="__copykat_d${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
esac
PS0='\e]133;C\a'
PS1='\[\e]133;A\a\]'"${PS1}"'\[\e]133;B\a\]'
"""


def _with_shell_integration(cmd: list[str]) -> tuple[list[str], str | None]:
    """Wrap a bash launch so its prompt emits OSC 133 markers we split on.

    Returns the command to spawn and a temp rcfile to delete afterwards (or the
    command unchanged and ``None`` when the shell isn't bash).
    """
    if os.path.basename(cmd[0]) != "bash":
        return cmd, None  # only bash integration is implemented
    fd, path = tempfile.mkstemp(prefix="copykat-rc-", suffix=".bash")
    with os.fdopen(fd, "w") as f:
        f.write(_BASH_INTEGRATION)
    return [cmd[0], "--rcfile", path, "-i"], path
