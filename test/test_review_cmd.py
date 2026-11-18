"""bdnex review: the flagged matches, one by one."""

import io

import pytest
import yaml

from bdnex.library import AlbumInfo, Library
from bdnex.ui import check_cmd, review_cmd


class Choosing:
    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    def review_file(self, position, path, album, reason, file_cover, album_cover):
        self.seen.append((position, path, album, reason))
        answer = self.answers.pop(0)
        if answer == "abort":
            from bdnex.importer.session import ImportAborted
            raise ImportAborted(path)
        return answer


@pytest.fixture
def flagged(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    paths = []
    with Library(db) as lib:
        for n in (1, 2, 3):
            album = lib.add_album(AlbumInfo("bedetheque", str(n), f"Titre {n}", language="fr"))
            path = make_cbz(f"book{n}.cbz", pages=2 + n)
            lib.add_file(path, album)
            lib.flag_review(path, f"the cover differs from the album's ({n}0% < 40%)")
            paths.append(path)
    return db, paths


def rows(db):
    with Library(db) as lib:
        return {r["path"]: r for r in lib.query([], files=True)}


def test_nothing_to_review(tmp_path, monkeypatch):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(tmp_path / "lib.sqlite"):
        pass
    out = io.StringIO()
    assert review_cmd.run([], out=out, prompter=Choosing()) == 0 and "Nothing to review" in out.getvalue()


def test_list_only_lists_and_changes_nothing(flagged):
    db, paths = flagged
    out = io.StringIO()
    assert review_cmd.run(["--list"], out=out) == 0
    text = out.getvalue()
    assert all(p in text for p in paths) and "3 file(s) wait for a review" in text
    assert all(r["review"] for r in rows(db).values())


def test_each_answer_does_what_it_says(flagged, monkeypatch):
    db, paths = flagged
    seen = {}
    monkeypatch.setattr("bdnex.ui.import_cmd.run", lambda argv, out=None: seen.update(argv=argv) or 0)
    out = io.StringIO()
    prompter = Choosing("keep", "unlink", "rematch")
    assert review_cmd.run([], out=out, prompter=prompter) == 0
    now = rows(db)
    assert now[paths[0]]["review"] is None and now[paths[0]]["id"] is not None  # kept
    assert now[paths[1]]["review"] is None and now[paths[1]]["id"] is None  # unlinked
    assert seen["argv"] == [paths[2], "--match", "--force"]  # matched again, after the loop
    assert [s[0] for s in prompter.seen] == ["[1/3]", "[2/3]", "[3/3]"]


def test_skip_leaves_the_flag_and_abort_stops(flagged):
    db, paths = flagged
    out = io.StringIO()
    assert review_cmd.run([], out=out, prompter=Choosing("skip", "abort")) == 0
    assert "Aborted" in out.getvalue()
    assert all(r["review"] for r in rows(db).values())  # nothing was decided for good


def test_a_query_narrows_the_files(flagged):
    db, paths = flagged
    prompter = Choosing("skip")
    review_cmd.run(["filename:book2"], out=io.StringIO(), prompter=prompter)
    assert [s[1] for s in prompter.seen] == [paths[1]]


@pytest.fixture
def matched(tmp_path, monkeypatch, make_cbz):
    """Two files linked to complete albums: one right (author and year agree), one wrong (year and author disagree)."""
    db = tmp_path / "scan.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    good, bad = make_cbz("Nains (Dupont) [2015].cbz", pages=3), make_cbz("Even (Zidrou, Alexei) (2021).cbz", pages=4)
    with Library(db) as lib:
        right = lib.add_album(AlbumInfo("bedetheque", "1", "Nains", year=2015, credits={"Writer": ["Dupont, Marc"]}, language="fr"))
        wrong = lib.add_album(AlbumInfo("bedetheque", "2", "Even If", year=2012, credits={"Writer": ["Smith, John"]}, language="fr"))
        lib.add_file(good, right)
        lib.add_file(bad, wrong)
    return db, good, bad


def test_check_matches_lists_the_matches_that_look_wrong_and_changes_nothing(matched):
    db, good, bad = matched
    out = io.StringIO()
    assert check_cmd.run(["--matches"], out=out) == 1  # something to look at
    text = out.getvalue()
    assert "1 match(es) look wrong" in text and bad in text and "reissue" in text and good not in text
    assert all(r["review"] is None for r in rows(db).values())


def test_fix_flags_them_for_review(matched):
    db, good, bad = matched
    assert check_cmd.run(["--matches", "--fix"], out=io.StringIO()) == 0
    now = rows(db)
    assert now[good]["review"] is None and "looks doubtful" in now[bad]["review"]


def test_the_former_review_scan_still_flags_and_says_where_it_went(matched, capsys):
    db, good, bad = matched
    review_cmd.run(["--scan", "--list"], out=io.StringIO())
    assert "bdnex check --matches --fix" in capsys.readouterr().err
    assert "looks doubtful" in rows(db)[bad]["review"]


def test_list_as_json_gives_the_reason_for_a_script(matched):
    import json

    db, good, bad = matched
    check_cmd.run(["--matches", "--fix"], out=io.StringIO())
    out = io.StringIO()
    assert review_cmd.run(["--list", "--json"], out=out) == 0
    data = json.loads(out.getvalue())
    assert [d["path"] for d in data] == [bad] and "looks doubtful" in data[0]["why"]
