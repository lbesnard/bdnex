"""``bdnex review --apply FILE``: an assistant's decisions, written as the review screen writes them."""

import io
import json

import pytest
import yaml

from bdnex.library import AlbumInfo, Library, SeriesInfo, decisions
from bdnex.library import review_batch as batch
from bdnex.ui import review_cmd, verdicts


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db), "cover": {"check": False}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    files = {}
    with Library(db) as lib:
        series = SeriesInfo("bedetheque", "s1", "Nains")
        one = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin", series=series, volume=1, year=2015))
        two = lib.add_album(AlbumInfo("bedetheque", "2", "Forge", series=series, volume=2, year=2016))
        for name, album in (("keep.cbz", one), ("cut.cbz", one), ("move.cbz", one), ("wait.cbz", two)):
            path = make_cbz(name)
            lib.add_file(path, album)
            lib.log_import(path, "tagged", 70.0, album)
            files[name] = path
        files["url"] = lib.get_album(two)["url"]
    return db, files


def apply(lines, *extra):
    out = io.StringIO()
    code = review_cmd.run(["--apply", lines, *extra], out=out)
    return code, out.getvalue()


def write(tmp_path, rows):
    target = tmp_path / "decisions.tsv"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(target)


def test_parse_reads_tsv_json_comments_and_the_header():
    found = decisions.parse("path\tdecision\talbum\treason\n# a comment\n/a.cbz\tkeep\t\tclean\n"
                            + json.dumps({"path": "/b.cbz", "decision": "switch", "album": "7"}) + "\n")
    assert [(d.path, d.action, d.album, d.reason) for d in found] == [("/a.cbz", "keep", "", "clean"), ("/b.cbz", "switch", "7", "")]


@pytest.mark.parametrize("text", ["/a.cbz\tmaybe\n", "\tkeep\n", "/a.cbz\tswitch\n", "{not json}\n"])
def test_a_bad_line_is_refused_with_its_number(text):
    with pytest.raises(decisions.DecisionError, match="line 1"):
        decisions.parse(text)


def test_decisions_are_written_like_the_review_screen_writes_them(env, tmp_path, monkeypatch):
    db, files = env

    def switch(path, url, keep_both=False):  # `import --album` needs the album list: the test library has none
        with Library(db) as other:
            other.set_file_album(path, other.find_album("bedetheque", "2"))
        return True

    monkeypatch.setattr(verdicts, "rematch", switch)
    path = write(tmp_path, [f"{files['keep.cbz']}\tkeep\t\tcredits agree", f"{files['cut.cbz']}\tunlink\t\tthin",
                            f"{files['move.cbz']}\tswitch\t{files['url']}\tthe name says T2", f"{files['wait.cbz']}\tleave\t\t"])
    code, text = apply(path)
    assert code == 0 and "1 kept" in text and "1 switched" in text and "1 unlinked" in text and "1 left" in text
    with Library(db) as lib:
        attrs = lambda name: lib.attrs_of("file", lib.get_file(files[name])["id"]).get(batch.CHECKED)  # noqa: E731
        assert attrs("keep.cbz") == "keep" and attrs("cut.cbz") == "unlink" and attrs("wait.cbz") == "leave"
        assert lib.get_file(files["cut.cbz"])["album_id"] is None
        assert lib.get_file(files["keep.cbz"])["album_id"] is not None
        assert lib.get_file(files["move.cbz"])["album_id"] == lib.get_file(files["wait.cbz"])["album_id"]
        assert attrs("move.cbz").startswith("other:")


def test_pretend_changes_nothing(env, tmp_path):
    db, files = env
    code, text = apply(write(tmp_path, [f"{files['cut.cbz']}\tunlink\t\t"]), "-n")
    assert code == 0 and "Would be" in text and "1 unlinked" in text
    with Library(db, readonly=True) as lib:
        assert lib.get_file(files["cut.cbz"])["album_id"] is not None


def test_a_file_with_a_bad_line_applies_nothing(env, tmp_path):
    db, files = env
    code, _ = apply(write(tmp_path, [f"{files['cut.cbz']}\tunlink\t\t", "/x.cbz\tmaybe\t\t"]))
    assert code == 2
    with Library(db, readonly=True) as lib:
        assert lib.get_file(files["cut.cbz"])["album_id"] is not None


def test_an_unknown_file_is_counted_and_the_rest_goes_on(env, tmp_path):
    db, files = env
    code, text = apply(write(tmp_path, ["/nowhere.cbz\tkeep\t\t", f"{files['cut.cbz']}\tunlink\t\t"]))
    assert code == 0 and "1 not in the library" in text and "1 unlinked" in text
