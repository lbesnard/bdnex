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
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
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
    fields: tuple = ()      # what the page asks for: query, paths, import (its three choices), formats, older_than
    preview: bool = True    # has a preview (`-n`); else only "Run for real"
    asks: bool = False      # a real run needs the page to ask for a confirmation (it can not go ahead unasked)
    listed: bool = True     # a card on the Tasks page (else started from another view, such as the trash)


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
        if options.get("preview", True):
            argv = ["-n", *argv]
        elif not options.get("interactive"):
            argv = ["--force", *argv]  # nobody to ask: the page did not offer to ask
        if command == "move" and options.get("copy"):
            argv = ["--copy", *argv]
        if command == "convert":
            formats = options.get("formats") or []
            if not isinstance(formats, list) or any(fmt not in ("pdf", "cbr") for fmt in formats):
                raise JobError("`formats` is a list of `pdf` and `cbr`")
            argv = [arg for fmt in formats for arg in ("--from", fmt)] + argv
        return (lambda out: write_cmd.run(command, argv, out=out)), argv
    return build


def _import_command(options: dict, settings):
    """`bdnex import`: the steps, how much to ask, whether files may move, and folders inside the collection or a source."""
    from bdnex.ui import import_cmd

    preview = options.get("preview", True)
    argv = ["-n"] if preview else []
    steps = options.get("steps") or []
    if not isinstance(steps, list) or any(step not in ("match", "fetch") for step in steps):
        raise JobError("`steps` is a list of `match` and `fetch`")
    argv += [f"--{step}" for step in steps]
    auto = options.get("auto") or ""
    if auto not in ("", "strong", "all"):
        raise JobError("`auto` is `strong` or `all`")
    unattended = bool(options.get("unattended"))
    if unattended:
        argv.append("--unattended")  # the confident matches are applied, the others wait for you: nothing is asked
    elif not preview and not options.get("interactive") and auto != "all":
        auto = "all"  # nobody to ask: never ask
    if auto:
        argv.append(f"--auto={auto}")
    if options.get("sources"):
        if not settings.import_.sources:
            raise JobError("there is no import source in the configuration (`import.sources`)")
        argv.append("--sources")  # what is new in the configured sources, each in its own mode
    if options.get("skipped"):
        argv.append("--skipped")  # only the files skipped at an earlier import, asked about again
    where = options.get("files") or ""
    if where not in ("", "database_only", "no_move"):
        raise JobError("`files` is `database_only` or `no_move`")
    if where:
        argv.append("--" + where.replace("_", "-"))
    if options.get("force"):
        argv.append("--force")  # redo the steps for files that are already done: a rematch
    argv += _paths(options, settings)
    return (lambda out: import_cmd.run(argv, out=out)), argv


def _simple(module: str, extra: Callable[[dict, Any], list[str]]):
    def build(options: dict, settings):
        import importlib

        run = importlib.import_module(f"bdnex.ui.{module}").run
        argv = extra(options, settings)
        return (lambda out: run(argv, out=out)), argv
    return build


def _days(options: dict) -> list[str]:
    days = options.get("older_than")
    if days in (None, ""):
        return []
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 36500:
        raise JobError("`older_than` is a number of days")
    return ["--older-than", str(days)]


def _trash_id(options: dict) -> str:
    entry = options.get("id")
    if isinstance(entry, bool) or not isinstance(entry, int) or entry < 1:
        raise JobError("`id` is the number of a trash entry")
    return str(entry)


def _two_folders(options: dict) -> list[str]:
    """`old` and `new`: folders as text (nothing is read or moved on the disk: only the stored paths change)."""
    found = []
    for key in ("old", "new"):
        value = options.get(key)
        if not isinstance(value, str) or not value.strip() or value.startswith("-") or "\n" in value:
            raise JobError(f"`{key}` is a folder")
        found.append(value.strip())
    return found


def _batch(options: dict) -> list[str]:
    batch = str(options.get("batch") or "last")
    if not all(c.isalnum() or c in "-_" for c in batch):
        raise JobError("`batch` is a run name from `bdnex undo`, or `last`")
    return [batch]


def _preview(options: dict) -> list[str]:
    return ["-n"] if options.get("preview", True) else []


SPECS: dict[str, Spec] = {
    "check": Spec("Check the library", False, _simple("check_cmd", lambda o, s: [])),
    "check-fix": Spec("Check the library and settle what is safe", True, _simple("check_cmd", lambda o, s: ["--fix"])),
    "duplicates": Spec("Find duplicates", False, _simple("duplicates_cmd", lambda o, s: [])),
    "init": Spec("Download the album list (init)", True, _simple("init_cmd", lambda o, s: []), preview=False),
    "record": Spec("Record files as they are", True,
                   _simple("import_cmd", lambda o, s: ["--as-is", *_preview(o), *_paths(o, s)]), fields=("paths",)),
    "import": Spec("Match and read new files (import)", True, _import_command, fields=("paths", "import")),
    "watch": Spec("Watch the import sources (automatic import)", True, _import_command, listed=False),
    "reparse": Spec("Update albums from the stored pages (reparse)", True,
                    _simple("reparse_cmd", lambda o, s: [*_preview(o), *_terms(o)]), fields=("query",)),
    "write": Spec("Write tags into the files", True, _write_command("write"), fields=("query",)),
    "move": Spec("Write tags and move the files into the collection", True, _write_command("move"), fields=("query",)),
    "convert": Spec("Convert PDFs (or CBRs) to CBZ", True, _write_command("convert"), fields=("query", "formats")),
    "repair": Spec("Repair damaged archives", True, _write_command("repair"), fields=("query",)),
    "relocate": Spec("Tell the library your comics moved (relocate)", True,
                     _simple("relocate_cmd", lambda o, s: [*_preview(o), *_two_folders(o)]), fields=("relocate",)),
    "undo": Spec("Take back the files a move run renamed", True,
                 _simple("undo_cmd", lambda o, s: [*_batch(o), *_preview(o)])),
    "relink": Spec("Find files that were moved by hand", True,
                   _simple("relink_cmd", lambda o, s: [*_preview(o), *_paths(o, s)]), fields=("paths",)),
    "trash-empty": Spec("Empty the trash (delete for good)", True,
                        _simple("trash_cmd", lambda o, s: ["--empty", *_days(o), *(["-n"] if o.get("preview", True) else
                                                                                   [] if o.get("interactive") else ["--yes"])]),
                        fields=("older_than",)),
    "trash-restore": Spec("Restore a file from the trash", True,
                          _simple("trash_cmd", lambda o, s: ["--restore", _trash_id(o), *(["--place"] if o.get("place") else []),
                                                              *_preview(o)]), listed=False),
    "reset": Spec("Forget the whole library (reset)", True,
                  _simple("reset_cmd", lambda o, s: _preview(o)), asks=True),
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
    interactive: bool = False        # questions are asked on the page (else the run never asks)
    question: Optional[dict] = None  # the question waiting for an answer: {id, kind, message, choices...}
    question_image: Optional[bytes] = field(default=None, repr=False)  # the picture shown with it (a PNG)
    _answer: Optional[dict] = field(default=None, repr=False)
    _answered: threading.Event = field(default_factory=threading.Event, repr=False)
    _question_ids: Any = field(default_factory=lambda: itertools.count(1), repr=False)

    def ask(self, question: dict, image: Optional[bytes] = None) -> dict:
        """Called by the job's own thread: put ``question`` on the job and wait until the page answers it.
        Cancelling the job raises ``KeyboardInterrupt`` here (the wait wakes up twice a second to let it in)."""
        self._answered.clear()
        self._answer = None
        self.question_image = image
        self.question = {"id": next(self._question_ids), "image": bool(image), **question}
        try:
            while not self._answered.wait(0.5):
                pass
            return self._answer or {}
        finally:
            self.question = None
            self.question_image = None

    def answer(self, question_id: int, answer: dict) -> bool:
        """The page's answer to the question with this id; ``False`` when it is not the question being asked."""
        current = self.question
        if current is None or current["id"] != question_id or self._answered.is_set():
            return False
        count = len(current.get("choices") or [])
        picked = answer.get("choices") or []
        if current["kind"] == "select" and not (answer.get("choice") is not None and 0 <= answer["choice"] < count):
            raise JobError("pick one of the answers offered")
        if current["kind"] == "search" and answer.get("choice") is not None and not 0 <= answer["choice"] < count:
            raise JobError("pick one of the answers offered")
        if current["kind"] == "checkbox" and not all(0 <= i < count for i in picked):
            raise JobError("pick among the answers offered")
        if len(answer.get("text") or "") > 2000:
            raise JobError("the text is too long")
        self._answer = answer
        self._answered.set()
        return True

    def public(self, after: int = 0) -> dict[str, Any]:
        return {"id": self.id, "command": self.command, "label": self.label, "status": self.status,
                "started": self.started, "finished": self.finished, "exit_code": self.exit_code,
                "arguments": self.argv, "lines": self.lines[max(0, after - self.dropped):],
                "next": self.dropped + len(self.lines), "interactive": self.interactive, "question": self.question}


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


STORED_JOBS = 30     # earlier runs kept across a restart
STORED_LINES = 300   # lines kept of each


class JobManager:
    def __init__(self, settings, store: Optional[Path] = None):
        self.settings = settings
        self.jobs: dict[int, Job] = {}
        self._lock = threading.Lock()      # protects the logs and the table of jobs
        self._running = threading.Lock()   # one job at a time
        self._store = store or Path(str(settings.library_path) + ".jobs.json")  # beside the library (a volume in Docker)
        self._load()
        self._ids = itertools.count(max(self.jobs, default=0) + 1)

    def _load(self) -> None:
        """The earlier runs, so that the list survives a restart. A run that was going when the server stopped did not
        finish: it says so (what it did stays done; run it again to continue)."""
        try:
            records = json.loads(self._store.read_text(encoding="utf-8"))
            for record in records:
                job = Job(record["id"], record["command"], record["label"], record["argv"], status=record["status"],
                          started=record["started"], finished=record.get("finished"), exit_code=record.get("exit_code"),
                          lines=list(record.get("lines", [])))
                if job.status == "running":
                    job.status, job.finished = "interrupted", job.finished or time.time()
                    job.lines.append("(the server stopped while this was running: run it again to continue)")
                self.jobs[job.id] = job
        except (OSError, ValueError, KeyError, TypeError):
            self.jobs = {}  # no history, or an unreadable one: start without

    def _save(self) -> None:
        try:
            recent = sorted(self.jobs.values(), key=lambda job: job.id)[-STORED_JOBS:]
            records = [{"id": j.id, "command": j.command, "label": j.label, "argv": j.argv, "status": j.status, "started": j.started,
                        "finished": j.finished, "exit_code": j.exit_code, "lines": j.lines[-STORED_LINES:]} for j in recent]
            tmp = self._store.with_name(self._store.name + ".tmp")
            tmp.write_text(json.dumps(records), encoding="utf-8")
            os.replace(tmp, self._store)
        except OSError:
            pass  # the history is a convenience: never a reason for a job to fail

    def waiting(self) -> Optional[dict]:
        """The job that waits for an answer, with its question, or ``None``."""
        for job in self.jobs.values():
            question = job.question
            if job.status == "running" and question:
                return {"job": job.id, "question": question["id"], "message": question["message"]}
        return None

    def start(self, command: str, options: Optional[dict] = None, allow_changes: bool = False) -> Job:
        spec = SPECS.get(command)
        if spec is None:
            raise JobError(f"unknown job {command!r}; the jobs are: {', '.join(SPECS)}")
        options = options or {}
        preview = bool(options.get("preview", True))
        if preview and not spec.preview:
            raise JobError(f"{command!r} has no preview: run it for real")
        if not preview and spec.asks and not options.get("interactive"):
            raise JobError(f"{command!r} asks you to confirm: tick \"ask me my questions here\" (it never runs unasked)")
        if spec.writes and not preview and not allow_changes:
            raise PermissionError("changes are disabled: set `api.allow_changes: true` in the configuration")
        function, argv = spec.build(options, self.settings)
        interactive = bool(options.get("interactive")) and not preview and spec.writes
        if not self._running.acquire(blocking=False):
            raise Busy("another job is running: wait for it to finish, or cancel it")
        with self._lock:
            job = Job(next(self._ids), command, spec.label, argv, interactive=interactive)
            self.jobs[job.id] = job
        self._save()
        threading.Thread(target=self._run, args=(job, function), daemon=True, name=f"bdnex-job-{job.id}").start()
        return job

    def _run(self, job: Job, function: Callable) -> None:
        log = _Log(job, self._lock)
        job.thread_id = threading.get_ident()
        try:
            from bdnex.ui import prompts

            if job.interactive:
                from bdnex.api.webprompter import WebPrompter

                factory = lambda **kw: WebPrompter(job.ask, **kw)  # noqa: E731
            else:
                factory = None
            with contextlib.redirect_stderr(log), contextlib.redirect_stdout(log), prompts.use_prompter(factory):
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
            self._save()
            self._running.release()

    def get(self, job_id: int) -> Optional[Job]:
        return self.jobs.get(job_id)

    def all(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda job: -job.id)

    def answer(self, job_id: int, question_id: int, answer: dict) -> bool:
        job = self.jobs.get(job_id)
        return bool(job and job.answer(question_id, answer))

    def cancel(self, job_id: int) -> bool:
        """Ask a running job to stop, as Ctrl-C would. What it already did stays done."""
        job = self.jobs.get(job_id)
        if job is None or job.status != "running" or job.thread_id is None:
            return False
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(job.thread_id), ctypes.py_object(KeyboardInterrupt))
        return True

    def busy(self) -> bool:
        return any(job.status == "running" for job in self.jobs.values())
