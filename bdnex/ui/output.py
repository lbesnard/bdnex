"""Console feedback shared by the commands: progress lines and logging."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time

from bdnex.formatting import human_duration


def _tint(message: str, on: bool) -> str:
    """Colour the kinds of progress line the commands print (only on a terminal)."""
    from bdnex.ui import color

    if not on:
        return message
    counter = re.match(r"^(\[\d+/\d+\]|\[page \d+/\d+\]) (.*)$", message)
    if counter:
        return f"{color.paint(counter.group(1), 'cyan', on)} {color.paint(counter.group(2), 'bold', on)}"
    stripped = message.lstrip()
    for prefix, style in (("match:", "green"), ("cover:", "dim")):
        percent = re.search(r"(\d+)%", stripped)
        if stripped.startswith(prefix) and percent:  # the number in the colour of the score, the rest as before
            at = len(message) - len(stripped)
            return (message[:at] + color.paint(stripped[:percent.start()], style, on)
                    + color.paint_score(percent.group(0), float(percent.group(1)), on)
                    + color.paint(stripped[percent.end():], style, on))
    for prefix, style in (("match:", "green"), ("Series folder", "cyan_bold"), ("page unavailable", "red"),
                          ("page read", "dim"), ("fetching the album page", "dim"), ("album already complete", "dim"),
                          ("Interrupted", "yellow"), ("Resuming", "yellow"), ("Back to", "yellow")):
        if stripped.startswith(prefix):
            return message[:len(message) - len(stripped)] + color.paint(stripped, style, on)
    return message


def progress(message: str) -> None:
    """A live status line on stderr (stdout stays clean for the final report)."""
    from bdnex.ui import color

    print(_tint(message, color.enabled(sys.stderr)), file=sys.stderr, flush=True)


def banner(title: str, step: int = 0, steps: int = 0, stream=None) -> None:
    """A heading between the phases of a long command: a rule, ``Step 2/3  title``, and a blank line."""
    from bdnex.ui import color

    stream = stream or sys.stderr
    on = color.enabled(stream)
    label = f"Step {step}/{steps}" if steps > 1 else ""
    head = f"{color.paint(label, 'cyan_bold', on)}  " if label else ""
    print(f"\n{color.paint(color.rule(), 'dim', on)}\n{head}{color.paint(title, 'bold', on)}\n", file=stream, flush=True)


def add_verbosity(parser) -> None:
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="more detail: -v what is decided and why (every file), -vv how (reads, cache, requests, threads), "
                             "-vvv everything, libraries and SQL included")
    parser.add_argument("--log-file", metavar="PATH", action=_LogFile,
                        help="also write everything, at the debug level and with the time, to this file (for a long run)")


def add_pretend(parser, what: str = "be done") -> None:
    """``-n / --pretend``: the same flag and the same promise in every command (``what`` says what would happen)."""
    parser.add_argument("-n", "--pretend", action="store_true", help=f"show what would {what}; change nothing")


def add_workers(parser, unit: str, setting: str = "import.workers", note: str = "") -> None:
    """``--workers N``: how many files at once; ``unit`` says what is done to a file, ``setting`` is the default."""
    parser.add_argument("--workers", type=int, metavar="N",
                        help=f"{unit} at once (0: the cores less one{note}; 1: one by one); default: `{setting}`")


LOG_FILE: str | None = None  # set by `--log-file`: everything bdnex logs goes there, at the debug level


class _LogFile(argparse.Action):
    """``--log-file PATH``: remember the path; ``setup_logging`` opens it."""

    def __call__(self, parser, namespace, values, option_string=None):
        global LOG_FILE
        LOG_FILE = os.path.expanduser(values)
        setattr(namespace, self.dest, values)


def setup_logging(verbosity: int) -> None:
    """``-v``: what bdnex decides (info); ``-vv``: how (debug: reads, cache, requests, with the thread); ``-vvv``: all of it
    from every library too, and each SQL statement (for debugging bdnex itself). Without ``-vvv`` the libraries it uses
    stay at warnings. A ``--log-file`` gets bdnex's debug level, with the time."""
    own = logging.DEBUG if verbosity >= 2 else logging.INFO if verbosity == 1 else logging.WARNING
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(own)
    from bdnex.ui import color

    console.setFormatter(color.LogFormatter(
        "%(levelname)s [%(threadName)s] %(name)s: %(message)s" if verbosity >= 2 else "%(levelname)s %(name)s: %(message)s",
        color.enabled(sys.stderr)))
    handlers: list[logging.Handler] = [console]
    if LOG_FILE:
        try:
            to_file = logging.FileHandler(LOG_FILE, encoding="utf-8")
        except OSError as err:
            print(f"warning: cannot write the log file {LOG_FILE}: {err}", file=sys.stderr)
        else:
            to_file.setLevel(logging.DEBUG)
            to_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"))
            handlers.append(to_file)
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, "_bdnex", False)]:  # ours from an earlier call (a job of `serve`, a test): not
        root.removeHandler(old)                                              # other code's handlers, and not twice
        old.close()
    for handler in handlers:
        handler._bdnex = True
        root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbosity >= 3 else logging.WARNING)
    logging.getLogger("bdnex").setLevel(logging.DEBUG if len(handlers) > 1 or verbosity >= 2 else own)
    # every SQL statement is a lot: only from -vvv (the file gets them too when it is asked for with -vvv)
    logging.getLogger(SQL_LOGGER).setLevel(logging.DEBUG if verbosity >= 3 else logging.INFO)


SQL_LOGGER = "bdnex.sql"


def _cells(text: str) -> int:
    """How many terminal columns ``text`` takes (wide characters count two, combining marks none)."""
    import unicodedata

    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


class Bar:
    """A progress bar with a percentage and an estimate of the time left.

    On a terminal one line is redrawn in place; elsewhere (a pipe, a log) a plain line is printed
    every 10 %, so the output stays readable. ``total`` may be an estimate: the bar never reaches
    100 % before :meth:`finish`.
    """

    def __init__(self, total: int, label: str = "", stream=None, width: int = 24, tty: bool | None = None,
                 clock=time.monotonic, exact: bool = True):
        self.total, self.label, self.width = max(1, total), label, width
        self.exact = exact  # False: ``total`` is a guess, so the bar stays under 100 % until finish()
        self.stream = stream or sys.stderr
        self.tty = self.stream.isatty() if tty is None else tty
        self.done = 0
        self._clock, self._start, self._last_decile = clock, clock(), -1
        self._shown = False

    # -- drawing
    def percent(self) -> int:
        pct = int(100 * self.done / self.total)
        return min(100, pct) if self.exact else min(99, pct)

    def line(self, detail: str = "") -> str:
        pct = self.percent()
        filled = int(self.width * pct / 100)
        eta = ""
        if self.done >= 2:
            left = (self._clock() - self._start) / self.done * max(0, self.total - self.done)
            eta = f"  about {human_duration(left)} left" if left >= 1 else ""
        bar = "█" * filled + "░" * (self.width - filled)
        from bdnex.ui import color

        if self.tty and color.enabled(self.stream):  # NO_COLOR and TERM=dumb are honoured here too
            bar = color.paint("█" * filled, "green", True) + color.paint("░" * (self.width - filled), "dim", True)
            return f"{color.paint(self.label, 'bold', True)} [{bar}] {pct:>3}%  {self.done}/{self.total}{eta}  {detail}".rstrip()
        return f"{self.label} [{bar}] {pct:>3}%  {self.done}/{self.total}{eta}  {detail}".rstrip()

    def _fit(self, text: str) -> str:
        """On a terminal, cut the line to its width (an ellipsis at the end), so that it is always one line."""
        import shutil

        width = shutil.get_terminal_size((100, 20)).columns - 1
        visible = re.sub(r"\x1b\[[0-9;]*m", "", text)
        if _cells(visible) <= width:
            return text
        kept, used = [], 0
        for char in visible:  # by columns, not characters: a wide (CJK, emoji) one takes two
            used += _cells(char)
            if used > max(1, width - 1):
                break
            kept.append(char)
        return "".join(kept) + "…"  # the colours of a line that long are not worth keeping

    def update(self, detail: str = "", step: int = 1) -> None:
        self.done += step
        if self.done > self.total:
            self.total = self.done + 1  # the estimate was low (only an estimate can be)
            self.exact = False
        text = self.line(detail)
        if self.tty:
            print("\r\x1b[K" + self._fit(text), end="", file=self.stream, flush=True)
            self._shown = True
        elif self.percent() // 10 > self._last_decile or self.total <= 10:
            self._last_decile = self.percent() // 10
            print(text, file=self.stream, flush=True)

    def refresh(self, detail: str = "") -> None:
        """Redraw the line with new text, without counting a step (a terminal only)."""
        if self.tty:
            print("\r\x1b[K" + self._fit(self.line(detail)), end="", file=self.stream, flush=True)
            self._shown = True

    def say(self, text: str, stream=None) -> None:
        """Print ``text`` on its own lines above the bar (the bar line is cleared first, and drawn again by the next
        update). ``stream`` is where the text goes (the bar's own stream by default)."""
        target = stream or self.stream
        if self.tty and self._shown:
            print("\r\x1b[K", end="", file=self.stream, flush=True)
            self._shown = False
        print(text, file=target, flush=True)

    def finish(self, message: str = "") -> None:
        """End the bar (clearing the line on a terminal) and print ``message``."""
        if self.tty and self._shown:
            print("\r\x1b[K", end="", file=self.stream, flush=True)
        if message:
            print(message, file=self.stream, flush=True)
        self._shown = False
