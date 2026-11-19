"""`bdnex recheck`: the matches applied under the strong threshold, weighed again."""

import io
import json

import pytest
import yaml

from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.ui import recheck_cmd


class Yes:
    def __init__(self, answer=True):
        self.answer, self.asked = answer, []

    def confirm(self, message):
        self.asked.append(message)
        return self.answer


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
            "kept": ("Nains - T01 - Redwin (Jarry) [2015].cbz", album, 81.0),   # the year and the author agree
            "wrong": ("Nains - T05 - Autre.cbz", album, 78.0),                   # another volume than the album's
            "unconfirmed": ("Nains - T02 - Forge.cbz", other, 82.0),             # nothing says anything
            "confident": ("Nains - T09 - Sure.cbz", album, 96.0),                # not weak: never looked at
            "chosen": ("Nains - T08 - Mine.cbz", album, None),                   # chosen by you: never touched
        }
        for name, (filename, target, score) in cases.items():
            path = make_cbz(filename)
            lib.add_file(path, target)
            lib.log_import(path, "tagged", score, target)
            files[name] = path
    return db, files


def run(*argv, prompter=None):
    out = io.StringIO()
    return recheck_cmd.run(list(argv), out=out, prompter=prompter), out.getvalue()


def test_each_weak_match_is_confirmed_contradicted_or_left_unconfirmed(env):
    _, files = env
    code, text = run("--json", "--no-cover")
    verdicts = {v["path"]: v["verdict"] for v in json.loads(text)}
    assert code == 0
    assert verdicts == {files["kept"]: "kept", files["wrong"]: "wrong", files["unconfirmed"]: "unconfirmed"}


def test_a_preview_changes_nothing_and_says_what_would_happen(env):
    db, files = env
    code, text = run("-n", "--no-cover", "--unlink")
    assert code == 0 and "1 would be unlinked, 1 flagged" in text
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is not None and not lib.get_file(files["wrong"])["review"]


def test_by_default_the_doubtful_ones_are_flagged_and_nothing_is_unlinked(env):
    db, files = env
    code, text = run("--no-cover")
    assert code == 0 and "Flagged 2 file(s)" in text
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["review"] and lib.get_file(files["unconfirmed"])["review"]
        assert not lib.get_file(files["kept"])["review"] and not lib.get_file(files["confident"])["review"]
        assert lib.get_file(files["wrong"])["album_id"] is not None


def test_unlink_takes_only_the_contradicted_ones_and_remembers_them_as_skipped(env):
    db, files = env
    prompter = Yes()
    code, text = run("--no-cover", "--unlink", prompter=prompter)
    assert code == 0 and prompter.asked and "Unlinked 1" in text and "Flagged 1" in text
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is None
        assert lib.last_import(files["wrong"])["outcome"] == "skipped"  # `import --skipped` picks it up again
        assert lib.get_file(files["unconfirmed"])["album_id"] is not None and lib.get_file(files["unconfirmed"])["review"]
        assert lib.get_file(files["chosen"])["album_id"] is not None and lib.get_file(files["kept"])["album_id"] is not None


def test_unconfirmed_goes_too_when_asked_and_a_no_stops_everything(env):
    db, files = env
    assert run("--no-cover", "--unlink", "--unconfirmed", prompter=Yes(False))[0] == 1
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is not None
    code, text = run("--no-cover", "--unlink", "--unconfirmed", "-y")
    with Library(db) as lib:
        assert code == 0 and "Unlinked 2" in text
        assert lib.get_file(files["unconfirmed"])["album_id"] is None and lib.get_file(files["kept"])["album_id"] is not None


def test_a_query_limits_it_and_a_bad_one_is_refused(env, capsys):
    _, files = env
    code, text = run("--json", "--no-cover", "filename:Autre")
    assert [v["path"] for v in json.loads(text)] == [files["wrong"]]
    assert run("--no-cover", "year:")[0] == 2


def test_the_cover_of_the_file_against_the_saved_cover_confirms_or_contradicts(env, monkeypatch):
    pytest.importorskip("cv2")
    import zipfile

    from importer.test_covers import art, jpeg

    from bdnex.config import load_config
    from bdnex.importer import covers

    monkeypatch.setattr(covers, "opencv_installed", lambda: True)  # the suite switches it off
    db, files = env
    settings = load_config().settings
    with Library(db) as lib:
        for name, seed in (("unconfirmed", 1), ("kept", 1), ("wrong", 1)):
            album = lib.get_album(lib.get_file(files[name])["album_id"])
            covers.save_cover(settings.cover_path, album["source"], album["source_id"], jpeg(art(1)))
    for name, seed in (("unconfirmed", 1), ("wrong", 2)):  # the first file's cover is the album's, the second's is another
        with zipfile.ZipFile(files[name], "w") as archive:
            archive.writestr("page-1.jpg", jpeg(art(seed)))
    code, text = run("--json")
    verdicts = {v["path"]: v for v in json.loads(text)}
    assert verdicts[files["unconfirmed"]]["verdict"] == "kept" and verdicts[files["unconfirmed"]]["cover"] >= 60
    assert verdicts[files["wrong"]]["verdict"] == "wrong" and verdicts[files["wrong"]]["cover"] < 40
    with Library(db) as lib:
        assert lib.get_file(files["unconfirmed"])["cover_score"] >= 60  # kept for the next time
