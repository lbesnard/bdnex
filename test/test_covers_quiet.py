"""The cover phase says what it is doing, and the image library's own warnings do not land in the middle of it."""
import io
import logging
import os
import subprocess
import sys

import pytest

from bdnex.importer import covers


def test_what_the_native_library_writes_to_stderr_is_caught_and_kept_for_debug(capfd, caplog):
    caplog.set_level(logging.DEBUG, logger="bdnex.importer.covers")
    with covers.quiet_native_stderr():
        os.write(2, b"Corrupt JPEG data: 1 extraneous bytes before marker 0xdb\n")  # what libjpeg does: a write to fd 2, not to sys.stderr
    os.write(2, b"after\n")
    seen = capfd.readouterr().err
    assert "Corrupt JPEG" not in seen and "after" in seen  # the warning is not on the screen, the rest still is
    assert any("Corrupt JPEG data" in record.message for record in caplog.records)


def test_a_broken_picture_still_raises_the_cover_error_inside_the_catch():
    pytest.importorskip("cv2")
    with pytest.raises(covers.CoverError):
        covers.sift_similarity(b"not a picture", b"neither")


def test_the_cover_phase_shows_a_bar_and_says_how_it_went():
    from types import SimpleNamespace

    from bdnex.importer.session_covers import CoverPass

    class Bar:
        def __init__(self, total, label):
            self.total, self.label, self.updates, self.finished = total, label, [], False

        def update(self, detail="", step=1):
            self.updates.append(detail)

        def finish(self, message=""):
            self.finished = True

    lines, bars = [], []

    class Session(CoverPass):
        def __init__(self):
            self._covers, self._cover_jobs, self._cover_pool, self._cover_failed = [], [], None, 0
            self.source = SimpleNamespace(parallel=1, fetch_errors=(OSError,))
            self.verbosity = 1
            self.progress = lines.append
            self.bar = lambda total, label: bars.append(Bar(total, label)) or bars[-1]
            self.compared = []

        def _queue_cover(self, result):  # nothing to download: the end of the run compares
            self._covers.append(result)

        def _check_cover(self, result):
            self.compared.append(result)

    session = Session()
    session._covers = [SimpleNamespace(path=f"/x/Album {n}.cbz") for n in range(3)]
    session._check_covers()
    assert any("Covers: 3 to get and compare" in line for line in lines) and any("all 3 done" in line for line in lines)
    assert bars[0].total == 3 and bars[0].label == "Covers" and bars[0].finished and len(bars[0].updates) == 3
    assert bars[0].updates[0] == "Album 0.cbz" and len(session.compared) == 3
