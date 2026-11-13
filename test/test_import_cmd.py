import io
import os

import pytest
import yaml

from bdnex.importer.bedetheque import Bedetheque
from bdnex.ui import import_cmd
from helpers import ALBUM_URL, FakeWeb


@pytest.fixture
def setup(tmp_path, monkeypatch, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"),
                                    "import": {"auto": True},
                                    "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    web = FakeWeb()
    import bdnex.importer.bedetheque as bdt_plugin
    monkeypatch.setattr(bdt_plugin, "http_fetch", web)
    monkeypatch.setattr(bdt_plugin, "build_fetcher", lambda settings: web)
    original = Bedetheque.album_info
    monkeypatch.setattr(Bedetheque, "album_info",
                        lambda self, url, recache=False: original(self, ALBUM_URL, recache))
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    from bdnex.ui import init_cmd
    assert init_cmd.run([], out=io.StringIO(), bar_stream=io.StringIO()) == 0  # the album list, as `bdnex init`
    return make_cbz("Nains - T01 - Redwin de la forge.cbz"), tmp_path


def test_import_command_end_to_end(setup):
    path, tmp_path = setup
    out = io.StringIO()
    assert import_cmd.run([str(os.path.dirname(path))], out=out) == 0
    text = out.getvalue()
    assert "complete" in text and "1 file(s): 1 complete" in text

    from bdnex.library import Library
    with Library(tmp_path / "lib.sqlite") as lib:
        assert lib.stats()["albums"] == 1 and lib.get_file(path)["album_id"] is not None


def test_import_command_pretend_leaves_library_empty(setup):
    path, tmp_path = setup
    out = io.StringIO()
    assert import_cmd.run(["--pretend", path], out=out) == 0
    assert "pretend" in out.getvalue()
    from bdnex.library import Library
    with Library(tmp_path / "lib.sqlite") as lib:
        assert lib.stats()["files"] == 0 and lib.stats()["albums"] == 0


def test_import_command_rejects_invalid_config(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "bad.yaml"
    conf.write_text("config_version: 2\noops: 1\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert import_cmd.run(["x.cbz"]) == 1
    assert "oops" in capsys.readouterr().err


def test_main_dispatches_import(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 2\ncollection_path: ~/c\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    seen = {}
    monkeypatch.setattr("bdnex.ui.import_cmd.run", lambda argv: seen.setdefault("argv", argv) and 0)
    with pytest.raises(SystemExit) as exit_:
        ui.main(["import", "-n", "foo.cbz"])
    assert seen["argv"] == ["-n", "foo.cbz"] and exit_.value.code == 0


def test_import_command_shows_progress_on_stderr(setup, capsys):
    path, tmp_path = setup
    import_cmd.run([path], out=io.StringIO())
    err = capsys.readouterr().err
    assert "[1/1] Nains - T01 - Redwin de la forge.cbz" in err


def test_verbosity_levels():
    import logging
    from bdnex.ui.output import setup_logging
    for flags, level in ((0, logging.WARNING), (1, logging.INFO), (2, logging.DEBUG)):
        setup_logging(flags)
        assert logging.getLogger().level == level
    setup_logging(0)


def test_import_accepts_verbose_flag(setup):
    path, tmp_path = setup
    assert import_cmd.run(["-vv", "-n", path], out=io.StringIO()) == 0


def test_import_command_with_a_configured_foreign_source(tmp_path, monkeypatch, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"),
                                    "source": "helpers:create_fake_source"}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    out = io.StringIO()
    assert import_cmd.run(["--auto=all", make_cbz("Maus.cbz")], out=out) == 1  # no album list: `bdnex init` first
    monkeypatch.setattr("helpers.FakeSource.is_ready", lambda self: True)
    assert import_cmd.run(["--auto=all", make_cbz("Maus.cbz")], out=out) == 0
    assert "1 file(s): 1 complete" in out.getvalue()
    from bdnex.library import Library
    with Library(tmp_path / "lib.sqlite") as lib:
        assert lib.get_album(lib.find_album("fake", "200"))["title"] == "Maus"


def test_import_command_with_an_unknown_source(tmp_path, monkeypatch, capsys, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"), "source": "nope"}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert import_cmd.run([make_cbz("a.cbz")], out=io.StringIO()) == 1
    err = capsys.readouterr().err
    assert "unknown source 'nope'" in err and "bedetheque" in err and "config --edit" in err
def test_import_command_fetch_step_completes_matched_albums(setup, monkeypatch, capsys):
    path, tmp_path = setup
    original = Bedetheque.album_info

    def blocked(self, url, recache=False):
        raise FileNotFoundError("no page yet")

    monkeypatch.setattr(Bedetheque, "album_info", blocked)
    out = io.StringIO()
    import_cmd.run([path], out=out)
    assert "1 matched" in out.getvalue() and "`bdnex import --fetch` tries again" in out.getvalue()
    out = io.StringIO()
    import_cmd.run(["--match", path], out=out)  # only the match step: it is done already
    assert "1 already" in out.getvalue() and "--fetch" in out.getvalue()

    monkeypatch.setattr(Bedetheque, "album_info", lambda self, url, recache=False: original(self, ALBUM_URL, recache))
    out = io.StringIO()
    assert import_cmd.run(["--fetch", path], out=out) == 0
    assert "1 complete" in out.getvalue()
    out = io.StringIO()
    import_cmd.run(["--fetch", path], out=out)
    assert "1 already" in out.getvalue()
    from bdnex.library import Library
    with Library(tmp_path / "lib.sqlite") as lib:
        assert lib.query(["partial:true"]) == []


def test_import_with_a_mistyped_fetcher_prints_a_message_not_a_traceback(tmp_path, monkeypatch, capsys, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"), "bedetheque": {
        "fetcher": "bdnex.importer.bedetheque.flaresolverr_fetch:flaresolverr_fetch"}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert import_cmd.run([make_cbz("a.cbz")], out=io.StringIO()) == 1
    err = capsys.readouterr().err
    assert "Did you mean 'bdnex.importer.bedetheque:flaresolverr_fetch'?" in err
    assert "bedetheque.fetcher" in err and "Traceback" not in err


def test_report_describes_what_was_recorded(setup):
    path, _ = setup
    out = io.StringIO()
    import_cmd.run([path], out=out)
    text = out.getvalue()
    assert "complete" in text and "-> Nains - T01 - Redwin de la Forge (series)" in text
    assert "Jarry, Nicolas" in text and "Soleil Productions 2015" in text and "56 pages" in text
    assert "complete" in text and "bdnex ls --long" in text


def test_report_flags_partial_albums(setup, monkeypatch):
    path, _ = setup
    monkeypatch.setattr(Bedetheque, "album_info", lambda self, url, recache=False: (_ for _ in ()).throw(FileNotFoundError("x")))
    out = io.StringIO()
    import_cmd.run([path], out=out)
    assert "PARTIAL" in out.getvalue() and "partial: save the page" in out.getvalue()


def test_a_stray_character_argument_is_named_in_the_error(setup):
    path, _ = setup
    out = io.StringIO()
    assert import_cmd.run(["\u2502"], out=out) == 1
    assert "'\u2502' is not an existing file or folder" in out.getvalue()


def test_as_is_records_files_without_matching_or_touching_them(setup, make_cbz):
    path, tmp_path = setup
    folder = os.path.dirname(path)
    before = {name: os.path.getmtime(os.path.join(folder, name)) for name in os.listdir(folder) if name.endswith(".cbz")}
    out = io.StringIO()
    assert import_cmd.run(["--as-is", folder], out=out) == 0
    assert "1 recorded" in out.getvalue()
    from bdnex.config import load_config
    from bdnex.library import Library
    with Library.for_settings(load_config().settings) as library:
        row = library.get_file(path)
        assert row["album_id"] is None and row["md5"] and row["format"] == "cbz"
        assert library.last_import(path)["outcome"] == "registered"
        assert library.stats()["albums"] == 0
    assert before == {name: os.path.getmtime(os.path.join(folder, name)) for name in os.listdir(folder) if name.endswith(".cbz")}
    again = io.StringIO()
    import_cmd.run(["--as-is", folder], out=again)
    assert "1 already in the library" in again.getvalue()
    assert import_cmd.run([folder], out=io.StringIO()) == 0  # and a normal import matches them afterwards
    with Library.for_settings(load_config().settings) as library:
        assert library.get_file(path)["album_id"] is not None


def test_as_is_keeps_what_the_file_says_about_itself(setup, make_cbz):
    import zipfile
    path, tmp_path = setup
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("ComicInfo.xml", "<ComicInfo><Series>Nains</Series><Number>3</Number><Title>Foo</Title></ComicInfo>")
    import_cmd.run(["--as-is", os.path.dirname(path)], out=io.StringIO())
    from bdnex.config import load_config
    from bdnex.library import Library
    with Library.for_settings(load_config().settings) as library:
        assert library.get_file(path)["embedded"] == {"Series": "Nains", "Number": "3", "Title": "Foo"}
        assert [r["path"] for r in library.query(["embedded:nains"], files=True)] == [path]


def test_select_works_on_library_files_that_match_a_query(setup):
    path, tmp_path = setup
    folder = os.path.dirname(path)
    import_cmd.run(["--as-is", folder], out=io.StringIO())
    assert import_cmd.run(["--select", "tagged:false"], out=io.StringIO()) == 0  # no path: the unmatched ones
    from bdnex.config import load_config
    from bdnex.library import Library
    with Library.for_settings(load_config().settings) as library:
        album = library.get_file(path)["album_id"]
        assert album is not None
        title = library.get_album(album)["title"]
    out = io.StringIO()
    assert import_cmd.run(["--select", "tagged:false"], out=out) == 1  # nothing is left to select
    assert import_cmd.run(["--fetch", "--force", "--select", f"title:={title}"], out=io.StringIO()) == 0
    assert import_cmd.run(["--select", "title::("], out=io.StringIO()) == 2


def test_import_says_where_pages_come_from_when_no_fetcher_is_set(setup, capsys):
    path, tmp_path = setup
    import_cmd.run([os.path.dirname(path)], out=io.StringIO())
    assert "fetcher_endpoint` is not set" in capsys.readouterr().err


def test_skipped_earlier_files_are_left_alone_until_asked_about_with_skipped(setup):
    from bdnex.config import load_config
    from bdnex.library import Library
    path, tmp_path = setup
    with Library.for_settings(load_config().settings) as library:
        library.add_file(path)
        library.set_file_album(path, None)
        library.log_import(path, "skipped", 77.0)  # skipped at an earlier import
    out = io.StringIO()
    assert import_cmd.run([os.path.dirname(path)], out=out) == 0
    text = out.getvalue()
    assert "skipped earlier" in text and "`bdnex import --skipped`" in text  # one hint, not a note per file
    with Library.for_settings(load_config().settings) as library:
        assert library.get_file(path)["album_id"] is None
    out = io.StringIO()
    assert import_cmd.run(["--skipped"], out=out) == 0  # no folder needed: the skipped files are the files
    assert "1 file(s): 1 complete" in out.getvalue() or "tagged" in out.getvalue() or "complete" in out.getvalue()
    with Library.for_settings(load_config().settings) as library:
        assert library.get_file(path)["album_id"] is not None


def test_skipped_with_nothing_skipped_says_so(setup, capsys):
    assert import_cmd.run(["--skipped"], out=io.StringIO()) == 1
    assert "No file was skipped" in capsys.readouterr().err
