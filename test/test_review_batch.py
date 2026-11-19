"""Checking the doubtful matches a batch at a time: the selection and the decisions, with no screen."""

import pytest
import yaml

from bdnex.config import load_config
from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library import review_batch as batch


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db), "cover": {"check": False}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    files = {}
    with Library(db) as lib:
        series = SeriesInfo("bedetheque", "s1", "Nains")
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin", series=series, volume=1, year=2015,
                                        credits={"Writer": ["Jarry, Nicolas"]}))
        other = lib.add_album(AlbumInfo("bedetheque", "2", "Forge", series=series, volume=2, year=2016))
        cases = {
            "wrong": ("Nains - T05 - Autre.cbz", album, 78.0),            # another volume than the album's
            "unconfirmed": ("Nains - T02 - Forge.cbz", other, 82.0),      # nothing says anything
            "kept": ("Nains - T01 - Redwin (Jarry) [2015].cbz", album, 81.0),  # confirmed: not in the batch
            "confident": ("Nains - T09 - Sure.cbz", album, 96.0),         # strong and not flagged: never looked at
        }
        for name, (filename, target, score) in cases.items():
            path = make_cbz(filename)
            lib.add_file(path, target)
            lib.log_import(path, "tagged", score, target)
            files[name] = path
        loose = make_cbz("Loose file.cbz")
        lib.add_file(loose, None)
        lib.log_import(loose, "skipped", 55.0)
        files["loose"] = loose
    return db, files


def test_the_batch_is_the_doubtful_files_most_doubtful_first_and_can_include_the_unmatched(env):
    db, files = env
    settings = load_config().settings
    with Library(db) as lib:
        rows = batch.next_batch(lib, settings, size=50)
        assert [r.path for r in rows] == [files["wrong"], files["unconfirmed"]]  # contradicted first; confirmed and strong left out
        assert rows[0].kind == "wrong" and rows[0].album and rows[0].score == 78.0 and rows[0].folder
        assert any(text.startswith("- ") for text in rows[0].why)
        with_loose = batch.next_batch(lib, settings, size=50, unmatched=True)
        assert [r.kind for r in with_loose] == ["wrong", "unconfirmed", "unmatched"] and with_loose[2].album is None
        assert len(batch.next_batch(lib, settings, size=1)) == 1  # the size is a cap


def test_each_decision_is_written_at_once_and_a_checked_file_leaves_the_next_batch(env):
    db, files = env
    settings = load_config().settings
    with Library(db) as lib:
        rows = batch.next_batch(lib, settings, size=50, unmatched=True)
        wrong, unconfirmed, loose = rows
        lib.flag_review(wrong.path, "flagged by hand")
        batch.decide(lib, wrong, "unlink")
        batch.decide(lib, unconfirmed, "keep")
        batch.decide(lib, loose, "leave")
        assert wrong.status == "unlinked" and lib.get_file(wrong.path)["album_id"] is None
        assert lib.last_import(wrong.path)["outcome"] == "skipped"  # `import --skipped` decides it again
        assert lib.attrs_of("file", lib.get_file(wrong.path)["id"])["checked"] == "unlink"
        assert lib.attrs_of("file", lib.get_file(unconfirmed.path)["id"])["checked"] == "keep"
        assert lib.get_file(unconfirmed.path)["album_id"] is not None
        assert batch.next_batch(lib, settings, size=50, unmatched=True) == []  # nothing left: the next call continues later
    with Library(db) as lib:  # a new connection: it was saved
        assert lib.attrs_of("file", lib.get_file(files["wrong"])["id"])["checked"] == "unlink"


def test_a_switch_to_another_album_is_recorded_and_a_refusal_changes_nothing(env, monkeypatch):
    from bdnex.ui import verdicts

    db, files = env
    settings = load_config().settings
    with Library(db) as lib:
        row = batch.next_batch(lib, settings, size=50)[0]
        monkeypatch.setattr(verdicts, "rematch", lambda path, url, keep_both=False: True)  # a refused import: exit code 0, nothing changed
        ok, message = batch.link_to(lib, row, "https://www.bedetheque.com/BD-Forge-2.html")
        assert not ok and "refused" in message and not row.status
        assert batch.CHECKED not in lib.attrs_of("file", lib.get_file(row.path)["id"])

        def switch(path, url, keep_both=False):
            lib.set_file_album(path, lib.find_album("bedetheque", "2"))
            return True

        monkeypatch.setattr(verdicts, "rematch", switch)
        ok, message = batch.link_to(lib, row, "https://www.bedetheque.com/BD-Forge-2.html")
        assert ok and "Forge" in message and row.status == "linked"
        assert lib.attrs_of("file", lib.get_file(row.path)["id"])["checked"].startswith("other:")


def test_an_unknown_decision_is_refused(env):
    db, _ = env
    with Library(db) as lib:
        row = batch.next_batch(lib, load_config().settings, size=1)[0]
        with pytest.raises(ValueError):
            batch.decide(lib, row, "nonsense")


def test_all_adds_the_files_nothing_says_are_doubtful_after_the_doubtful_ones_and_a_query_limits_them(env):
    db, files = env
    settings = load_config().settings
    with Library(db) as lib:
        rows = batch.next_batch(lib, settings, size=50, everything=True)
        assert [r.kind for r in rows] == ["wrong", "unconfirmed", "unmatched", "ok", "ok"]  # doubtful, no album, then the rest
        assert {r.path for r in rows[3:]} == {files["kept"], files["confident"]}
        assert rows[3].album and rows[3].why == []
        only = batch.next_batch(lib, settings, size=50, terms=["filename:Sure"], everything=True)
        assert [r.path for r in only] == [files["confident"]]
        assert len(batch.next_batch(lib, settings, size=4, everything=True)) == 4  # the size is a cap


def test_again_shows_the_files_already_checked_with_what_was_decided(env):
    db, files = env
    settings = load_config().settings
    with Library(db) as lib:
        wrong = batch.next_batch(lib, settings, size=50)[0]
        batch.decide(lib, wrong, "keep")
        assert wrong.path not in [r.path for r in batch.next_batch(lib, settings, size=50)]
        again = batch.next_batch(lib, settings, size=50, again=True)
        assert wrong.path in [r.path for r in again]
        assert next(r for r in again if r.path == wrong.path).previous == "keep"


def test_the_batch_options_need_batch(env, capsys):
    from bdnex.ui import review_cmd

    for flag in ("--all", "--again", "--unmatched"):
        assert review_cmd.run([flag]) == 2
    assert "go with --batch" in capsys.readouterr().err
