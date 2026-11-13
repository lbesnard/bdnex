"""Sizes, durations, paths and the shared options come from one place."""

import argparse

import pytest

from bdnex.formatting import human_duration, human_size, short_path
from bdnex.ui.output import add_pretend, add_workers


@pytest.mark.parametrize("size, text", [(0, "0 B"), (1023, "1023 B"), (1536, "1.5 KiB"), (5 * 1024 ** 2, "5.0 MiB"),
                                        (3 * 1024 ** 3, "3.0 GiB"), (5000 * 1024 ** 3, "5000.0 GiB")])
def test_human_size(size, text):
    assert human_size(size) == text


def test_short_path_cuts_the_library_folder_only():
    assert short_path("/lib/a/b.cbz", "/lib") == "a/b.cbz"
    assert short_path("/other/b.cbz", "/lib") == "/other/b.cbz" and short_path("/x", None) == "/x"
    assert human_duration(45) == "45 s" and human_duration(600) == "10 min"


def test_every_command_gets_the_same_pretend_and_workers_flags():
    parser = argparse.ArgumentParser()
    add_pretend(parser, "be linked")
    add_workers(parser, "files read", "write.workers", ", at most 4")
    args = parser.parse_args(["-n", "--workers", "3"])
    assert args.pretend and args.workers == 3
    text = parser.format_help()
    assert "show what would be linked; change nothing" in text and "default: `write.workers`" in text
