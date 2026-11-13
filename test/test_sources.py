"""Import sources: copied or moved into the collection, remembered, never modified when read-only."""

import io
import os
import shutil

import pytest
import yaml

from bdnex.config import load_config
from bdnex.library import Library
from bdnex.ui import import_cmd, relocate_cmd, write_cmd

from test_import_cmd import setup  # noqa: F401  (the album list and the fake web)


@pytest.fixture
def sources(setup):  # noqa: F811
    """A collection folder and two sources: `inbox` (copy) and `dl` (move), each holding the same book once."""
    path, tmp_path = setup
    inbox, dl, comics = tmp_path / "inbox", tmp_path / "dl", tmp_path / "comics"
    inbox.mkdir(), dl.mkdir(), comics.mkdir()
    shutil.move(path, inbox / os.path.basename(path))
    conf = tmp_path / "bdnex.yaml"
    config = yaml.safe_load(conf.read_text())
    config.update(collection_path=str(comics))
    config["import"] = {"auto": True, "sources": [{"path": str(inbox), "mode": "copy"}, {"path": str(dl), "mode": "move"}]}
    conf.write_text(yaml.safe_dump(config))
    return tmp_path, inbox, dl, comics


def run(*argv):
    out = io.StringIO()
    code = import_cmd.run(list(argv), out=out)
    return code, out.getvalue()


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_a_copy_source_is_copied_and_never_touched(sources):
    tmp, inbox, dl, comics = sources
    book = inbox / "Nains - T01 - Redwin de la forge.cbz"
    before = book.read_bytes()
    code, text = run("--sources")
    assert code == 0 and book.read_bytes() == before and book.exists()  # the original stays, unchanged
    copies = tree(comics)
    assert len(copies) == 1 and copies[0].startswith("Series/")
    with Library.for_settings(load_config().settings) as lib:
        row = lib.get_file(str(comics / copies[0]))
        assert row["album_id"] is not None and lib.get_file(str(book)) is None  # the library tracks the copy
        origin = lib.get_origin(str(book))
        assert origin["path"] == str(comics / copies[0]) and origin["size"] == len(before)


def test_importing_the_source_again_skips_what_was_copied(sources):
    tmp, inbox, dl, comics = sources
    run("--sources")
    code, text = run("--sources", "-v")
    assert code == 0 and "already copied to" in text and len(tree(comics)) == 1
    book = inbox / "Nains - T01 - Redwin de la forge.cbz"
    with open(book, "ab") as fh:
        fh.write(b"edited at the source")
    code, text = run("--sources", "-v")
    assert "changed at the source since" in text and len(tree(comics)) == 1  # reported, not copied again


def test_a_move_source_is_emptied(sources):
    tmp, inbox, dl, comics = sources
    book = next(inbox.iterdir())
    shutil.move(book, dl / book.name)
    code, text = run("--sources")
    assert code == 0 and not (dl / book.name).exists() and len(tree(comics)) == 1


def test_files_under_a_source_are_stored_as_at_alias_and_followed_by_relocate(sources, capsys):
    tmp, inbox, dl, comics = sources
    book = next(inbox.iterdir())
    run("--as-is", str(inbox))
    with Library.for_settings(load_config().settings) as lib:
        stored = lib.conn.execute("SELECT path FROM files").fetchone()[0]
        assert stored == f"@inbox/{book.name}" and lib.get_file(str(book))["path"] == str(book)
    moved = tmp / "inbox-new"
    os.rename(inbox, moved)
    cfg = yaml.safe_load((tmp / "bdnex.yaml").read_text())
    cfg["import"]["sources"][0]["path"] = str(moved)
    (tmp / "bdnex.yaml").write_text(yaml.safe_dump(cfg))
    out = io.StringIO()
    assert relocate_cmd.run([str(inbox), str(moved)], out=out) == 0 and "source 'inbox'" in out.getvalue()
    with Library.for_settings(load_config().settings) as lib:
        assert lib.get_file(str(moved / book.name)) is not None and lib.missing_files() == []
        assert lib.conn.execute("SELECT path FROM files").fetchone()[0] == f"@inbox/{book.name}"  # nothing was rewritten


def test_a_read_only_source_is_never_written_or_moved(sources):
    tmp, inbox, dl, comics = sources
    book = next(inbox.iterdir())
    before = book.read_bytes()
    run("--as-is", str(inbox))
    with Library.for_settings(load_config().settings) as lib:
        album = lib.add_album(__import__("bdnex.library", fromlist=["AlbumInfo"]).AlbumInfo(
            "bedetheque", "9", "Un livre", language="fr"))
        lib.add_file(str(book), album)
    for command in ("write", "move"):
        out = io.StringIO()
        write_cmd.run(command, ["--force"], out=out)
        assert "read-only source 'inbox'" in out.getvalue(), command
    assert book.read_bytes() == before and book.exists()
    out = io.StringIO()
    write_cmd.run("move", ["--force", "--copy"], out=out)  # copying it into the collection is allowed
    assert len(tree(comics)) == 1 and book.read_bytes() == before


def test_the_command_line_wins_over_a_sources_mode(sources):
    tmp, inbox, dl, comics = sources
    book = next(inbox.iterdir())
    code, text = run("--sources", "--no-move")  # leave everything where it is
    assert tree(comics) == [] and book.exists()


def test_two_sources_with_the_same_folder_name_are_refused(tmp_path):
    from bdnex.config import ConfigError, build_settings
    with pytest.raises(ConfigError, match="same name"):
        build_settings({"config_version": 2, "import": {"sources": [{"path": "/a/in"}, {"path": "/b/in"}]}})
    build_settings({"config_version": 2, "import": {"sources": [{"path": "/a/in"}, {"path": "/b/in", "name": "other"}]}})


def test_a_folder_name_that_starts_with_an_at_sign_is_not_taken_for_a_source(tmp_path):
    with Library(tmp_path / "l.sqlite", root=tmp_path / "coll") as lib:
        odd = tmp_path / "coll" / "@inbox" / "a.cbz"
        odd.parent.mkdir(parents=True)
        odd.write_bytes(b"x")
        assert lib._store(str(odd)) == "./@inbox/a.cbz" and lib._resolve("./@inbox/a.cbz") == str(odd)


def test_adding_a_source_converts_the_paths_stored_under_it(tmp_path, make_cbz):
    folder = tmp_path / "dl"
    folder.mkdir()
    book = shutil.move(make_cbz("a.cbz"), folder / "a.cbz")
    with Library(tmp_path / "l.sqlite") as lib:
        lib.add_file(str(book))
        assert lib.conn.execute("SELECT path FROM files").fetchone()[0] == str(book)  # absolute, before
        assert lib.add_root("dl", folder)
        assert lib.conn.execute("SELECT path FROM files").fetchone()[0] == "@dl/a.cbz"
        assert lib.get_file(str(book)) is not None and not lib.add_root("dl", tmp_path / "elsewhere")


def test_the_collection_and_the_sources_must_be_separate_folders(tmp_path):
    from bdnex.config import ConfigError, build_settings

    def settings(collection, *folders):
        return build_settings({"config_version": 2, "collection_path": str(collection),
                               "import": {"sources": [{"path": str(f), "name": f"s{i}"} for i, f in enumerate(folders)]}})

    comics, inbox = tmp_path / "comics", tmp_path / "inbox"
    for folder in (comics, inbox):
        folder.mkdir()
    settings(comics, inbox)  # fine
    with pytest.raises(ConfigError, match="is your collection_path"):
        settings(comics, comics)
    with pytest.raises(ConfigError, match="collection_path .* is inside the source"):
        settings(comics, tmp_path)
    with pytest.raises(ConfigError, match="source 's0' .* is inside your collection_path"):
        settings(comics, comics / "incoming")
    with pytest.raises(ConfigError, match="are nested"):
        settings(comics, inbox, inbox / "sub")
    assert settings(comics, tmp_path / "comics2")  # a folder that only starts with the same letters is another folder
    build_settings({"config_version": 2, "import": {"sources": [{"path": "/x/in"}]}})  # placeholder collection: nothing to compare
