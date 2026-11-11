"""Console feedback shared by the commands: progress lines and logging."""

from __future__ import annotations

import logging
import sys


def progress(message: str) -> None:
    """A live status line on stderr (stdout stays clean for the final report)."""
    print(message, file=sys.stderr, flush=True)


def add_verbosity(parser) -> None:
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="more detail: -v for info, -vv for debug (requests, cache hits)")


def setup_logging(verbosity: int) -> None:
    level = logging.WARNING if verbosity <= 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)-7s %(name)s: %(message)s", stream=sys.stderr, force=True)
