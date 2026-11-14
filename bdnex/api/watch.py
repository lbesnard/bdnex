"""Watching the import sources: ``bdnex serve`` looks at them by itself and imports what is new, asking nothing.

Only when ``watch.enabled`` is true (read at every look, so the settings page can turn it on and off). Confident matches
are applied, the others wait for you (Tasks: import, "only files skipped earlier"), and a look is skipped while another
task runs. The run is a task like any other: its output is in the list of runs.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from bdnex.api.jobs import Busy, JobError, JobManager

logger = logging.getLogger(__name__)

FIRST_LOOK = 45.0   # seconds after the server starts
POLL = 30.0         # seconds between two checks of the settings while waiting


class Watcher:
    def __init__(self, settings, jobs: JobManager, wait: Optional[Callable[[float], bool]] = None):
        self.settings, self.jobs = settings, jobs
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait   # returns True when asked to stop (a test passes its own)
        self.last_job: Optional[int] = None
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
            self.look()
            delay = max(1, self.settings.watch.interval) * 60.0

    def look(self) -> bool:
        """One look: start an automatic import when it is wanted and possible. ``True`` when one was started."""
        if not self.settings.watch.enabled or not self.settings.import_.sources:
            return False
        try:
            job = self.jobs.start("watch", {"preview": False, "sources": True, "unattended": True}, allow_changes=True)
        except Busy:
            return False  # something else is running: the next look will do
        except (JobError, PermissionError) as err:
            logger.warning(f"the folder watch could not start: {err}")
            return False
        self.last_job = job.id
        return True
