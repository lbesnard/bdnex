"""Watching the import sources: ``bdnex serve`` looks at them by itself and imports what is new, asking nothing.

Only when ``watch.enabled`` is true (read at every look, so the settings page can turn it on and off). Confident matches
are applied, the others wait for you (Tasks: import, "only files skipped earlier"), and a look is skipped while another
task runs. The run is a task like any other: its output is in the list of runs.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

from bdnex.api.jobs import Busy, JobError, JobManager

logger = logging.getLogger(__name__)

MAX_BACKOFF = 6    # a failing run doubles the wait before the next look, up to 2**6 times the interval (a day at 15 minutes)
FIRST_LOOK = 45.0   # seconds after the server starts
POLL = 30.0         # seconds between two checks of the settings while waiting


class Watcher:
    def __init__(self, settings, jobs: JobManager, wait: Optional[Callable[[float], bool]] = None):
        self.settings, self.jobs = settings, jobs
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait   # returns True when asked to stop (a test passes its own)
        self.last_job: Optional[int] = None
        self.failures = 0  # runs in a row that failed (the site or the fetcher is down, a disk is gone): the wait grows
        self._missing_said = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self.run, daemon=True, name="bdnex-watch")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        """Sleep in short steps (so a change of the settings is seen), look when the time has come, and again after the interval."""
        delay = FIRST_LOOK
        while True:
            step = min(delay, POLL)
            if self._wait(step):
                return
            delay -= step
            if delay > 0:
                continue
            try:
                self.look()
            except Exception:  # whatever it is: the watch goes on (a dead thread would stop it silently until the server restarts)
                logger.exception("the folder watch failed; it will look again")
                self.failures += 1
            delay = max(1, self.settings.watch.interval) * 60.0 * 2 ** min(self.failures, MAX_BACKOFF)

    def _learn_from_last_run(self) -> None:
        """How the previous run ended: a failure lengthens the wait before the next look, a success resets it."""
        previous = self.jobs.get(self.last_job) if self.last_job is not None else None
        if previous is None or previous.status == "running":
            return
        self.failures = self.failures + 1 if previous.status == "failed" else 0

    def look(self) -> bool:
        """One look: start an automatic import when it is wanted and possible. ``True`` when one was started."""
        if not self.settings.watch.enabled or not self.settings.import_.sources:
            return False
        self._learn_from_last_run()
        present = [source for source in self.settings.import_.sources if os.path.isdir(source.folder)]
        if not present:  # an unmounted disk or an empty mount point: a run would only fail (or hammer the site) every interval
            if not self._missing_said:
                logger.warning("the folder watch is waiting: none of the import sources is there (%s)",
                               ", ".join(source.folder for source in self.settings.import_.sources))
                self._missing_said = True
            return False
        self._missing_said = False
        try:
            job = self.jobs.start("watch", {"preview": False, "sources": True, "unattended": True}, allow_changes=True)
        except Busy:
            return False  # something else is running: the next look will do
        except (JobError, PermissionError) as err:
            logger.warning(f"the folder watch could not start: {err}")
            return False
        self.last_job = job.id
        return True
