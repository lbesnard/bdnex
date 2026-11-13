"""A damaged archive fails its own file in one line, with no traceback."""

import logging

from bdnex.organizer import archive, organizer


def test_a_damaged_archive_is_a_one_line_failure(caplog):
    caplog.set_level(logging.INFO)
    result = organizer._failure({"path": "/x/a.cbr"}, archive.ArchiveError("a.cbr: Corrupt file - CRC check failed"))
    assert result.outcome == organizer.FAILED
    assert "CRC check failed" in result.note and "bdnex repair" in result.note
    assert not caplog.records  # nothing printed over the progress bar


def test_an_unexpected_error_still_logs_its_traceback(caplog):
    try:
        raise KeyError("bug")
    except KeyError as err:
        organizer._failure({"path": "/x/a.cbz"}, err)
    assert caplog.records and caplog.records[0].exc_info
