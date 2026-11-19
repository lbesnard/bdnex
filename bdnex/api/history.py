"""A record of what the web page and the API changed: who, when, what (``GET /api/history``).

One JSON line per change in ``<library>.changes.jsonl`` beside the library (like the jobs file: a volume in Docker). It is
only a log for people to read back; nothing reads it to decide anything. The last ``KEEP`` lines are kept.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

KEEP = 2000


class History:
    def __init__(self, path: Path, keep: int = KEEP):
        self.path, self.keep = Path(path), keep
        self._lock = threading.Lock()

    def record(self, who: str, method: str, path: str, status: int, detail: Optional[dict[str, Any]] = None) -> None:
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "who": who, "method": method, "path": path,
                           "status": status, "detail": detail or {}}, ensure_ascii=False)
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                self._trim()
        except OSError:
            pass  # a log is a help: a read-only folder must not stop a change

    def _trim(self) -> None:
        if self.path.stat().st_size < self.keep * 400:  # most lines are far shorter: look only when the file is big
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) > self.keep * 1.25:
            self.path.write_text("\n".join(lines[-self.keep:]) + "\n", encoding="utf-8")

    def recent(self, limit: int = 100, offset: int = 0) -> tuple[int, list[dict[str, Any]]]:
        """``(how many, a window of them)``, newest first."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0, []
        entries = []
        for line in reversed(lines):
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue  # a half-written line
        return len(entries), entries[offset:offset + limit]
