"""Commands of your own that run after bdnex has done something (``hooks:`` in the configuration)."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys

logger = logging.getLogger(__name__)

EVENTS = ("after_import", "after_write", "after_move")
TIMEOUT = 600  # seconds a hook may take


def run_hooks(settings, event: str, changed: int, command: str, stream=None) -> int:
    """Run the commands listed under ``hooks.<event>``; returns how many ran. ``changed`` is how many files or albums
    the command changed (nothing runs when it is 0). A hook that fails or takes too long is reported and never makes the
    command fail. Commands are split like a shell would split them but **not run by a shell**: no pipes, no ``&&``;
    write a small script for that."""
    commands = getattr(settings.hooks, event, []) if hasattr(settings, "hooks") else []
    if not commands or not changed:
        return 0
    stream = stream or sys.stderr
    # a hook is a command of yours, but not one that needs the web page's password or token: they are not handed on
    env = {**{k: v for k, v in os.environ.items() if k not in ("BDNEX_PASSWORD", "BDNEX_TOKEN", "BDNEX_COMICVINE_API_KEY")},
           "BDNEX_EVENT": event, "BDNEX_COMMAND": command, "BDNEX_CHANGED": str(changed)}
    ran = 0
    for text in commands:
        try:
            argv = shlex.split(text)
            print(f"hook {event}: {text}", file=stream)
            done = subprocess.run(argv, env=env, timeout=TIMEOUT, check=False)
            ran += 1
            if done.returncode:
                print(f"hook {event}: `{text}` ended with code {done.returncode}", file=stream)
        except (OSError, ValueError, subprocess.TimeoutExpired) as err:
            print(f"hook {event}: `{text}` could not run: {err}", file=stream)
            logger.debug("hook failed", exc_info=True)
    return ran
