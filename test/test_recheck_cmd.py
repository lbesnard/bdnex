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
    assert code == 0 and "1 unlinked, 0 flagged" in text
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is not None and not lib.get_file(files["wrong"])["review"]


def test_by_default_only_the_contradicted_are_flagged_and_nothing_is_unlinked(env):
    db, files = env
    code, text = run("--no-cover")
    assert code == 0 and "Flagged 1 file(s)" in text
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["review"] and not lib.get_file(files["unconfirmed"])["review"]
        assert not lib.get_file(files["kept"])["review"] and not lib.get_file(files["confident"])["review"]
        assert lib.get_file(files["wrong"])["album_id"] is not None


def test_unlink_takes_only_the_contradicted_ones_and_remembers_them_as_skipped(env):
    db, files = env
    prompter = Yes()
    code, text = run("--no-cover", "--unlink", prompter=prompter)
    assert code == 0 and prompter.asked and "Unlinked 1" in text and "Flagged" not in text
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is None
        assert lib.last_import(files["wrong"])["outcome"] == "skipped"  # `import --skipped` picks it up again
        assert lib.get_file(files["unconfirmed"])["album_id"] is not None and not lib.get_file(files["unconfirmed"])["review"]
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


def test_unconfirmed_is_flagged_only_when_asked_and_an_old_flag_of_recheck_is_taken_back(env):
    db, files = env
    with Library(db) as lib:
        lib.flag_review(files["unconfirmed"], "applied at 82%: nothing confirms it")
        lib.flag_review(files["kept"], "something a person wrote")
    code, text = run("--no-cover")
    assert "Took back 1 flag" in text
    with Library(db) as lib:
        assert not lib.get_file(files["unconfirmed"])["review"]
        assert lib.get_file(files["kept"])["review"] == "something a person wrote"  # not its own: left
    run("--no-cover", "--flag-unconfirmed")
    with Library(db) as lib:
        assert lib.get_file(files["unconfirmed"])["review"]


def _better(monkeypatch, files, url="https://www.bedetheque.com/BD-Autre-2.html"):
    """A better album found for the contradicted file (the cover comparison is tested with a real picture elsewhere)."""
    def fake(settings, library, verdicts, limit, say=lambda text: None):
        for v in verdicts:
            if v.path == files["wrong"]:
                v.better = {"url": url, "album": "Nains Forge", "score": 80, "cover": 88.0, "current_cover": 3.0}
        return 1

    monkeypatch.setattr(recheck_cmd, "look_for_better", fake)
    return url


def test_a_better_album_is_proposed_and_linked_only_with_rematch(env, monkeypatch):
    db, files = env
    url = _better(monkeypatch, files)
    code, text = run("-n", "--rematch")
    assert "another album's cover is clearly the file's" in text and "better: Nains Forge" in text
    assert "1 would be switched to a better album" in text
    code, text = run()  # flagged, with the proposal in the reason, nothing switched
    with Library(db) as lib:
        assert "better: Nains Forge" in lib.get_file(files["wrong"])["review"]
    called = []
    monkeypatch.setattr(recheck_cmd, "rematch", lambda path, address: called.append((path, address)) or True)
    code, text = run("--rematch", "-y")
    assert called == [(files["wrong"], url)] and "Switched 1 file(s)" in text


def test_review_auto_settles_the_flagged_files_by_rule(env, monkeypatch):
    from bdnex.ui import review_cmd

    db, files = env
    with Library(db) as lib:
        for name in ("kept", "wrong", "unconfirmed"):
            lib.flag_review(files[name], "its cover is 5% like the album's")
    out = io.StringIO()
    assert review_cmd.run(["--auto", "--no-cover", "-y"], out=out) == 0
    with Library(db) as lib:
        assert not lib.get_file(files["kept"])["review"]  # confirmed: the flag goes
        assert lib.get_file(files["wrong"])["album_id"] is None  # contradicted, nothing better: unlinked, skipped
        assert lib.last_import(files["wrong"])["outcome"] == "skipped"
        assert lib.get_file(files["unconfirmed"])["review"] and lib.get_file(files["unconfirmed"])["album_id"] is not None
    assert "1 still flagged" in out.getvalue()


def test_review_auto_preview_changes_nothing(env):
    from bdnex.ui import review_cmd

    db, files = env
    with Library(db) as lib:
        lib.flag_review(files["wrong"], "x")
    out = io.StringIO()
    assert review_cmd.run(["--auto", "--no-cover", "-n"], out=out) == 0
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is not None and lib.get_file(files["wrong"])["review"] == "x"
    assert "1 flagged file(s)" in out.getvalue()


class _Cand:
    def __init__(self, source_id, score):
        self.candidate = type("C", (), {"source_id": source_id, "url": f"https://x/BD-{source_id}.html", "name": f"album {source_id}"})()
        self.score = score


def _compare_with(monkeypatch, covers_of, matches):
    """The candidates and their covers, as the real comparison would give them (it is tested with pictures elsewhere)."""
    from bdnex.importer import covers, matching, registry
    from bdnex.ui import candidates_cmd

    source = type("S", (), {"name": "bedetheque", "is_ready": lambda self: True})()
    monkeypatch.setattr(covers, "opencv_installed", lambda: True)
    monkeypatch.setattr(registry, "create_source", lambda *a, **k: source)
    monkeypatch.setattr(matching, "find_matches", lambda src, path, limit=5: matches)

    def compare(src, settings, path, fmt, found, entries):
        for match, entry in zip(found, entries):
            entry["cover"] = covers_of[match.candidate.source_id]

    monkeypatch.setattr(candidates_cmd, "compare_covers", compare)


def _wrong_verdict(env, library):
    from bdnex.config import load_config
    from bdnex.library.recheck import weak_matches

    _, files = env
    settings = load_config().settings
    verdicts = [v for v in weak_matches(library, settings, [], compare=False) if v.path == files["wrong"]]
    return settings, verdicts


def test_the_best_other_cover_wins_only_when_it_looks_like_the_file_and_is_clearly_ahead(env, monkeypatch):
    db, files = env
    with Library(db) as lib:
        settings, verdicts = _wrong_verdict(env, lib)
        current = lib.find_album("bedetheque", "1")
        matches = [_Cand("1", 85), _Cand("2", 80), _Cand("7", 78)]
        _compare_with(monkeypatch, {"1": 3.0, "2": 85.0, "7": 20.0}, matches)
        assert recheck_cmd.look_for_better(settings, lib, verdicts, 5) == 1
        assert verdicts[0].better["url"].endswith("BD-2.html") and verdicts[0].better["cover"] == 85.0
        # two covers alike: no clear winner; and a cover under the vote minimum is no winner either
        verdicts[0].better = None
        _compare_with(monkeypatch, {"1": 3.0, "2": 85.0, "7": 80.0}, matches)
        assert recheck_cmd.look_for_better(settings, lib, verdicts, 5) == 0
        _compare_with(monkeypatch, {"1": 0.0, "2": 40.0, "7": 10.0}, matches)
        assert recheck_cmd.look_for_better(settings, lib, verdicts, 5) == 0
        assert current is not None and recheck_cmd.look_for_better(settings, lib, verdicts, 0) == 0  # 0: off


def test_each_file_and_each_candidate_cover_is_said(env, monkeypatch):
    db, files = env
    with Library(db) as lib:
        settings, verdicts = _wrong_verdict(env, lib)
        _compare_with(monkeypatch, {"1": 3.0, "2": 85.0, "7": 20.0}, [_Cand("1", 85), _Cand("2", 80), _Cand("7", 78)])
        lines = []
        recheck_cmd.look_for_better(settings, lib, verdicts, 5, say=lines.append)
    text = "\n".join(lines)
    assert "[1/1] Nains - T05 - Autre.cbz" in text and "now: Nains Redwin" in text
    assert "* cover   3%" in text and "cover  85%" in text and "-> better:" in text
    assert "Covers compared: 1 better album(s) found" in text
