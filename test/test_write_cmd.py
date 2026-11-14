import io
import os
import zipfile

import pytest
import yaml

from bdnex.importer.bedetheque import Bedetheque
from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.ui import import_cmd, write_cmd
from helpers import ALBUM_URL, FakeWeb


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db), "collection_path": str(tmp_path / "comics"),
                                    "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db) as lib:
        a = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin de la Forge",
                                    series=SeriesInfo("bedetheque", "47467", "Nains"), volume=1, language="fr",
                                    credits={"Writer": ["Jarry, Nicolas"]}))
        b = lib.add_album(AlbumInfo("bedetheque", "2", "Bran de Pierre",
                                    series=SeriesInfo("bedetheque", "47467", "Nains"), volume=2, language="fr",
                                    credits={"Writer": ["Jarry, Nicolas"]}))
        files = {"a": make_cbz("one.cbz", pages=3), "b": make_cbz("two.cbz", pages=4)}
        lib.add_file(files["a"], a)
        lib.add_file(files["b"], b)
        lib.add_file(make_cbz("loose.cbz", pages=5))
    return tmp_path, files


def run(command, *argv):
    out = io.StringIO()
    code = write_cmd.run(command, list(argv), out=out)
    return code, out.getvalue()


def has_comicinfo(path):
    with zipfile.ZipFile(path) as zf:
        return "ComicInfo.xml" in zf.namelist()


def test_write_all_tagged_files(env):
    tmp, files = env
    code, text = run("write", "--force")
    assert code == 0 and "2 written" in text
    assert has_comicinfo(files["a"]) and has_comicinfo(files["b"])
    assert not has_comicinfo(str(tmp / "loose.cbz"))  # never matched: left alone


def test_write_with_query(env):
    tmp, files = env
    code, text = run("write", "--force", "volume:2")
    assert "1 written" in text and has_comicinfo(files["b"]) and not has_comicinfo(files["a"])


def test_write_pretend(env):
    tmp, files = env
    code, text = run("write", "-n", "--force")
    assert code == 0 and "2 pretend" in text and not has_comicinfo(files["a"])


def test_move_into_library_tree_and_copy(env):
    tmp, files = env
    code, text = run("move", "--force", "volume:1")
    dest = tmp / "comics" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"
    assert code == 0 and dest.exists() and not os.path.exists(files["a"])
    code, text = run("move", "--force", "--copy", "volume:2")
    assert "1 copied" in text and os.path.exists(files["b"])


def test_move_requires_a_directory(env, monkeypatch, tmp_path):
    conf = tmp_path / "nodir.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert run("move")[0] == 1


def test_errors(env, monkeypatch, tmp_path, capsys):
    assert run("write", "year:abc")[0] == 2 and "bad query" in capsys.readouterr().err
    conf = tmp_path / "none.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "missing.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert run("write")[0] == 1 and "bdnex import" in capsys.readouterr().err


def test_main_dispatches_write_and_move(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 2\ncollection_path: ~/c\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    seen = []
    monkeypatch.setattr("bdnex.ui.write_cmd.run", lambda cmd, argv: seen.append((cmd, argv)) or 0)
    for cmd in ("write", "move"):
        with pytest.raises(SystemExit):
            ui.main([cmd, "series:nains"])
    assert seen == [("write", ["series:nains"]), ("move", ["series:nains"])]


# ----------------------------------------------------------- import -W/--move/--copy
@pytest.fixture
def import_env(tmp_path, monkeypatch, make_cbz):
    def configure(extra=None):
        conf = tmp_path / "bdnex.yaml"
        conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"),
                                        "collection_path": str(tmp_path / "comics"),
                                        "bedetheque": {"delay": [0, 0]}, **(extra or {}),
                                        "import": {"auto": True, **(extra or {}).get("import", {})}}))
        monkeypatch.setenv("BDNEX_CONFIG", str(conf))

    configure()
    web = FakeWeb()
    import bdnex.importer.bedetheque as bdt_plugin
    monkeypatch.setattr(bdt_plugin, "http_fetch", web)
    monkeypatch.setattr(bdt_plugin, "build_fetcher", lambda settings: web)
    original = Bedetheque.album_info
    monkeypatch.setattr(Bedetheque, "album_info", lambda self, url, recache=False: original(self, ALBUM_URL, recache))
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    from bdnex.ui import init_cmd
    assert init_cmd.run([], out=io.StringIO(), bar_stream=io.StringIO()) == 0  # the album list, as `bdnex init`
    path = make_cbz("Nains - T01 - Redwin de la forge.cbz")
    return path, tmp_path, configure


def do_import(*argv):
    out = io.StringIO()
    code = import_cmd.run(list(argv), out=out)
    return code, out.getvalue()


def test_plain_import_does_not_touch_the_file(import_env):
    path, tmp, _ = import_env
    code, text = do_import(path)
    assert code == 0 and not has_comicinfo(path) and "Files:" not in text


def test_import_with_write_flag(import_env):
    path, tmp, _ = import_env
    code, text = do_import("-W", "--force", path)
    assert code == 0 and has_comicinfo(path) and "1 written" in text


def test_import_with_move_flag(import_env):
    path, tmp, _ = import_env
    code, text = do_import("--move", "--force", path)
    dest = tmp / "comics" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"
    assert code == 0 and dest.exists() and not os.path.exists(path)
    with Library(tmp / "lib.sqlite", root=tmp / "comics") as lib:
        assert lib.get_file(str(dest)) is not None  # the library tracks the new location


def test_import_mode_copy_from_config(import_env):
    path, tmp, configure = import_env
    configure({"import": {"mode": "copy"}})
    code, text = do_import("--force", path)
    assert code == 0 and os.path.exists(path) and "1 copied" in text


def test_import_pretend_does_no_file_operations(import_env):
    path, tmp, _ = import_env
    code, text = do_import("-n", "--move", path)
    assert code == 0 and os.path.exists(path) and not (tmp / "comics").exists()


def test_import_move_without_directory_explains_and_keeps_the_import(import_env, capsys):
    path, tmp, configure = import_env
    conf = tmp / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp / "lib.sqlite"),
                                    "import": {"auto": True}, "bedetheque": {"delay": [0, 0]}}))
    code, text = do_import("--move", path)
    assert code == 1 and os.path.exists(path)
    assert "collection_path" in capsys.readouterr().err
    with Library(tmp / "lib.sqlite") as lib:
        assert lib.stats()["albums"] == 1  # the match itself was recorded


# ------------------------------------------------------------------ damaged files
def test_import_records_a_damaged_but_readable_file(import_env, make_damaged_cbz):
    _, tmp, _ = import_env
    path, _ = make_damaged_cbz("Nains - T01 - Redwin de la forge.cbz")
    code, text = do_import(path)
    assert code == 0 and "1 complete" in text
    assert "archive damaged (page p01.jpg failed its checksum)" in text and "bdnex repair" in text
    with Library(tmp / "lib.sqlite") as lib:
        assert lib.get_file(path)["corrupted"] == 1 and lib.get_file(path)["album_id"] is not None
        assert [r["path"] for r in lib.query(["corrupted:true"], files=True)] == [path]


def test_import_still_fails_on_a_file_that_cannot_be_opened(import_env):
    _, tmp, _ = import_env
    bad = tmp / "Nains - T01 - bad.cbz"
    bad.write_bytes(b"not an archive")
    code, text = do_import(str(bad))
    assert code == 1 and "1 failed" in text


def test_repair_command_end_to_end(import_env, make_damaged_cbz):
    _, tmp, _ = import_env
    path, originals = make_damaged_cbz("Nains - T01 - Redwin de la forge.cbz")
    do_import(path)
    out = io.StringIO()
    assert write_cmd.run("repair", ["-n"], out=out) == 0
    assert "1 pretend" in out.getvalue()
    out = io.StringIO()
    assert write_cmd.run("repair", ["--force"], out=out) == 0
    assert "1 repaired" in out.getvalue()
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
    with Library(tmp / "lib.sqlite") as lib:
        assert lib.get_file(path)["corrupted"] == 0
    out = io.StringIO()
    write_cmd.run("repair", [], out=out)
    assert "nothing to do" in out.getvalue()  # nothing damaged any more


def test_move_force_leaves_unrepairable_files_alone(import_env, make_damaged_cbz):
    _, tmp, _ = import_env
    path, _ = make_damaged_cbz("Nains - T01 - Redwin de la forge.cbz", damage="junk")
    do_import(path)
    out = io.StringIO()
    assert write_cmd.run("move", ["--force"], out=out) == 0
    assert "1 skipped" in out.getvalue() and "can not be repaired" in out.getvalue() and os.path.exists(path)


def test_main_dispatches_repair(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 2\ncollection_path: ~/c\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    seen = []
    monkeypatch.setattr("bdnex.ui.write_cmd.run", lambda cmd, argv: seen.append(cmd) or 0)
    with pytest.raises(SystemExit):
        ui.main(["repair"])
    assert seen == ["repair"]


# ---------------------------------------------------------------------------------- the preview
def tree(root):
    return sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("command", ["write", "move"])
def test_a_preview_changes_no_file_and_not_the_library(env, command):
    tmp, files = env
    before = tree(tmp)
    code, text = run(command, "-n")
    assert code == 0 and "2 pretend" in text and "Preview only: nothing was changed" in text
    assert tree(tmp) == before  # no file written, moved, created or touched; the database file included


def test_a_move_preview_shows_where_each_file_would_go(env):
    tmp, files = env
    code, text = run("move", "-n")
    assert "to    " in text and "Series/Nains/Nains - T01 - Redwin de la Forge.cbz" in text
    assert "from  " in text and text.count("would do") == 2
    assert os.path.exists(files["a"])  # still where it was


def test_a_write_preview_says_the_file_stays_where_it_is(env):
    tmp, files = env
    code, text = run("write", "-n")
    assert text.count("stays where it is") == 2 and "write ComicInfo.xml" in text


def test_the_library_is_opened_read_only_for_a_preview(env, monkeypatch):
    seen = []
    real = Library.for_settings.__func__
    monkeypatch.setattr(Library, "for_settings", classmethod(lambda cls, s, readonly=False: (seen.append(readonly), real(cls, s, readonly))[1]))
    run("move", "-n")
    run("move", "--force")
    assert seen == [True, False]


# ------------------------------------------------------------------ bdnex convert
def _add_pdf(tmp, name="three.pdf"):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=200, height=300).insert_text((20, 50), "a page")
    path = str(tmp / name)
    doc.save(path)
    doc.close()
    with Library(tmp / "lib.sqlite") as lib:
        lib.add_file(path, 2)  # the second album of the fixture
    return path


def test_convert_makes_a_cbz_of_a_pdf_even_when_the_config_tags_pdfs(env):
    tmp, files = env
    pdf = _add_pdf(tmp)
    conf = tmp / "bdnex.yaml"
    settings = yaml.safe_load(conf.read_text())
    conf.write_text(yaml.safe_dump({**settings, "write": {"pdf": "tag", "delete_original": "no"}}))
    code, text = run("convert", "--force")
    assert code == 0 and "1 written" in text
    cbz = str(tmp / "three.cbz")
    assert has_comicinfo(cbz) and os.path.exists(pdf)  # the original is kept (delete_original: no)
    assert not has_comicinfo(files["a"])  # a CBZ is not what `convert` is for


def test_convert_preview_changes_nothing_and_cbz_files_are_not_selected(env):
    tmp, files = env
    _add_pdf(tmp)
    code, text = run("convert", "-n")
    assert code == 0 and "1 pretend" in text and not os.path.exists(str(tmp / "three.cbz"))
    assert "1 file(s)" in text


def test_convert_refuses_when_nothing_is_to_be_converted(env):
    tmp, _ = env
    conf = tmp / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({**yaml.safe_load(conf.read_text()), "write": {"format_preferred": "none"}}))
    code, _ = run("convert", "-n")
    assert code == 1


def test_main_dispatches_convert(monkeypatch, tmp_path):
    from bdnex import ui
    conf = tmp_path / "c.yaml"
    conf.write_text("config_version: 2\ncollection_path: ~/c\n")
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    seen = []
    monkeypatch.setattr("bdnex.ui.write_cmd.run", lambda cmd, argv: seen.append(cmd) or 0)
    with pytest.raises(SystemExit):
        ui.main(["convert"])
    assert seen == ["convert"]
