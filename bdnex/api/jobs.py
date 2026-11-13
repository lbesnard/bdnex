"""Jobs for the web page: the commands of the command line, run in a background thread.

A job is one of a short list of commands (never an arbitrary command line), with options that are checked: paths must
be inside the collection or a source, queries are plain terms. The command writes to a buffer that the page reads while
it runs. Only one job runs at a time, because two writers on one library and one set of files would not end well.
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import itertools
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

MAX_LINES = 5000


class JobError(ValueError):
    """The job asked for is not allowed or its options are wrong."""


class Busy(RuntimeError):
    """Another job is running."""


@dataclass
class Spec:
    label: str
    writes: bool            # changes files or the library (needs `api.allow_changes`)
    build: Callable[..., tuple[Callable, list[str]]]  # (options, settings) -> (function(out), argv)


def _terms(options: dict) -> list[str]:
    terms = options.get("query") or []
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms) or any(t.startswith("-") for t in terms):
        raise JobError("`query` must be a list of search terms")
    return terms


def _paths(options: dict, settings) -> list[str]:
    """Folders and files given by the page: only inside the collection or a source."""
    allowed = [str(settings.collection_path and os.path.abspath(os.path.expanduser(settings.collection_path)))]
    allowed += [source.folder for source in settings.import_.sources]
    chosen = []
    for path in options.get("paths") or []:
        if not isinstance(path, str):
            raise JobError("`paths` must be a list of paths")
        absolute = os.path.abspath(os.path.expanduser(path))
        if not any(root and (absolute == root or absolute.startswith(root.rstrip("/") + os.sep)) for root in allowed):
            raise JobError(f"{path} is outside your collection and your import sources")
        chosen.append(absolute)
    return chosen


def _write_command(command: str):
    def build(options: dict, settings):
        from bdnex.ui import write_cmd

        argv = _terms(options)
        argv = ["-n", *argv] if options.get("preview", True) else ["--force", *argv]  # never ask: there is nobody to ask
        if command == "move" and options.get("copy"):
            argv = ["--copy", *argv]
        return (lambda out: write_cmd.run(command, argv, out=out)), argv
    return build


def _simple(module: str, extra: Callable[[dict, Any], list[str]]):
    def build(options: dict, settings):
        import importlib

        run = importlib.import_module(f"bdnex.ui.{module}").run
        argv = extra(options, settings)
        return (lambda out: run(argv, out=out)), argv
    return build


def _batch(options: dict) -> list[str]:
    batch = str(options.get("batch") or "last")
    if not all(c.isalnum() or c in "-_" for c in batch):
        raise JobError("`batch` is a run name from `bdnex undo`, or `last`")
    return [batch]


SPECS: dict[str, Spec] = {
    "check": Spec("Check the library", False, _simple("check_cmd", lambda o, s: [])),
    "check-fix": Spec("Check the library and settle what is safe", True, _simple("check_cmd", lambda o, s: ["--fix"])),
    "duplicates": Spec("Find duplicates", False, _simple("duplicates_cmd", lambda o, s: [])),
    "record": Spec("Record files as they are", True,
                   _simple("import_cmd", lambda o, s: ["--as-is", *(["-n"] if o.get("preview", True) else []), *_paths(o, s)])),
    "write": Spec("Write tags into the files", True, _write_command("write")),
    "move": Spec("Write tags and move the files into the collection", True, _write_command("move")),
    "repair": Spec("Repair damaged archives", True, _write_command("repair")),
    "undo": Spec("Take back the files a move run renamed", True,
                 _simple("undo_cmd", lambda o, s: [*_batch(o), *(["-n"] if o.get("preview", True) else [])])),
    "relink": Spec("Find files that were moved by hand", True,
                   _simple("relink_cmd", lambda o, s: [*(["-n"] if o.get("preview", True) else []), *_paths(o, s)])),
}


@dataclass
class Job:
    id: int
    command: str
    label: str
    argv: list[str]
    status: str = "running"          # running | done | failed | cancelled
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    exit_code: Optional[int] = None
    lines: list[str] = field(default_factory=list)
    dropped: int = 0                 # lines forgotten once MAX_LINES were kept
    thread_id: Optional[int] = None

    def public(self, after: int = 0) -> dict[str, Any]:
        return {"id": self.id, "command": self.command, "label": self.label, "status": self.status,
                "started": self.started, "finished": self.finished, "exit_code": self.exit_code,
                "arguments": self.argv, "lines": self.lines[max(0, after - self.dropped):],
                "next": self.dropped + len(self.lines)}


class _Log(io.TextIOBase):
    """What the command writes becomes lines of the job (a carriage return, as a progress bar draws, replaces the line)."""

    def __init__(self, job: Job, lock: threading.Lock):
        self.job, self.lock, self._partial = job, lock, ""

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        with self.lock:
            buffer = self._partial + text
            *complete, self._partial = buffer.replace("\r\n", "\n").split("\n")
            for line in complete:
                self._add(line.split("\r")[-1])
        return len(text)

    def flush(self) -> None:
        with self.lock:
            if self._partial:
                self._add(self._partial.split("\r")[-1])
                self._partial = ""

    def _add(self, line: str) -> None:
        self.job.lines.append(line)
        if len(self.job.lines) > MAX_LINES:
            del self.job.lines[0]
            self.job.dropped += 1


class JobManager:
    def __init__(self, settings):
        self.settings = settings
        self.jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()      # protects the logs and the table of jobs
        self._running = threading.Lock()   # one job at a time

    def start(self, command: str, options: Optional[dict] = None, allow_changes: bool = False) -> Job:
        spec = SPECS.get(command)
        if spec is None:
            raise JobError(f"unknown job {command!r}; the jobs are: {', '.join(SPECS)}")
        options = options or {}
        preview = bool(options.get("preview", True))
        if spec.writes and not preview and not allow_changes:
            raise PermissionError("changes are disabled: set `api.allow_changes: true` in the configuration")
        function, argv = spec.build(options, self.settings)
        if not self._running.acquire(blocking=False):
            raise Busy("another job is running: wait for it to finish, or cancel it")
        with self._lock:
            job = Job(next(self._ids), command, spec.label, argv)
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, function), daemon=True, name=f"bdnex-job-{job.id}").start()
        return job

    def _run(self, job: Job, function: Callable) -> None:
        log = _Log(job, self._lock)
        job.thread_id = threading.get_ident()
        try:
            with contextlib.redirect_stderr(log), contextlib.redirect_stdout(log):
                try:
                    code = function(log)
                except SystemExit as stop:  # argparse said no
                    code = stop.code if isinstance(stop.code, int) else 2
            job.exit_code = code or 0
            job.status = "done" if not job.exit_code else "failed"
        except KeyboardInterrupt:
            job.status = "cancelled"
        except Exception as err:  # the job failed; the page shows why
            log.write(f"error: {err}\n")
            job.status = "failed"
        finally:
            log.flush()
            job.finished = time.time()
            self._running.release()

    def get(self, job_id: int) -> Optional[Job]:
        return self.jobs.get(job_id)

    def all(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda job: -job.id)

    def cancel(self, job_id: int) -> bool:
        """Ask a running job to stop, as Ctrl-C would. What it already did stays done."""
        job = self.jobs.get(job_id)
        if job is None or job.status != "running" or job.thread_id is None:
            return False
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(job.thread_id), ctypes.py_object(KeyboardInterrupt))
        return True

    def busy(self) -> bool:
        return any(job.status == "running" for job in self.jobs.values())
