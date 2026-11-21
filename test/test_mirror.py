"""`bdnex mirror`: the album list says what exists, the library what is here; the rest is fetched, with its series page and cover."""

import io

import pytest
import yaml

from bdnex.config import load_config
from bdnex.importer import mirror
from bdnex.importer.bedetheque import Bedetheque
from bdnex.library import Library
from bdnex.ui import init_cmd
from helpers import FakeWeb


@pytest.fixture
def world(tmp_path, monkeypatch):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    web = FakeWeb()
    library = Library(tmp_path / "lib.sqlite")
    source = Bedetheque(library, fetch=web, sleep=lambda s: None, parallel=2)
    source.update_sitemaps()
    yield web, library, source, load_config().settings
    library.close()


def only_redwin(found):
    return [c for c in found.to_fetch if c.source_id == "245127"]


def test_the_plan_says_what_the_list_has_and_the_library_lacks(world):
    web, library, source, settings = world
    found = mirror.plan(source, library)
    assert found.in_list > 1 and found.complete == 0 and len(found.missing) == found.in_list and not found.unread
    assert [c.source_id for c in found.missing].count("245127") == 1


def test_a_run_stores_the_album_its_series_and_its_cover_and_a_second_run_finds_nothing_left(world):
    web, library, source, settings = world
    wanted = only_redwin(mirror.plan(source, library))
    result = mirror.Mirror(source, library, settings, workers=2).run(wanted)
    assert result.albums == 1 and result.covers == 1 and not result.failed and not result.stopped
    album = library.get_album(library.find_album("bedetheque", "245127"))
    assert album["title"] == "Redwin de la Forge" and album["extra"].get("partial") is not True and album["cover_file"]
    assert library.cache_get("bedetheque", "series", next(u for u in web.calls if "serie-" in u))  # the series page is kept too
    again = mirror.plan(source, library)
    assert again.complete == 1 and all(c.source_id != "245127" for c in again.to_fetch) and not again.no_cover


def test_pages_are_stored_compressed_and_read_back_whole(tmp_path):
    with Library(tmp_path / "l.sqlite") as library:
        html = "<html>" + "<p>Les 40 commandements</p>" * 500 + "</html>"
        library.cache_put("bedetheque", "album", "u", html)
        stored = library.conn.execute("SELECT content FROM source_cache").fetchone()[0]
        assert len(stored) < len(html) / 5 and library.cache_get("bedetheque", "album", "u") == html.encode()
        library.conn.execute("INSERT INTO source_cache(source, kind, url, content) VALUES ('bedetheque','album','old',?)", (b"<html>old</html>",))
        assert library.cache_get("bedetheque", "album", "old") == b"<html>old</html>"  # a page stored before: still readable
        library.cache_put("bedetheque", "sitemap", "s", "<urlset/>")
        assert library.conn.execute("SELECT content FROM source_cache WHERE kind='sitemap'").fetchone()[0] == b"<urlset/>"


def test_failures_in_a_row_stop_the_run_and_say_why(world):
    web, library, source, settings = world
    wanted = [c for c in mirror.plan(source, library).missing if c.source_id != "245127"][:25]  # the fake web has no page for them
    result = mirror.Mirror(source, library, settings, workers=2).run(wanted)
    assert result.stopped and "failures in a row" in result.stopped and result.albums == 0 and result.left > 0


def test_the_preview_counts_and_fetches_nothing(world, capsys):
    web, library, source, settings = world
    library.close()
    before = len(web.calls)
    out = io.StringIO()
    assert init_cmd.run(["--mirror", "-n"], out=out, bar_stream=io.StringIO()) == 0
    assert "Album list" in out.getvalue() and "To do" in out.getvalue()
    assert len(web.calls) == before


def test_an_album_only_matched_is_fetched_too_and_a_missing_cover_alone_is_found(world):
    web, library, source, settings = world
    candidate = only_redwin(mirror.plan(source, library))[0]
    library.add_album(source.partial_info(candidate))  # what an import without --fetch leaves: the page is not read
    found = mirror.plan(source, library)
    assert [c.source_id for c in found.unread] == ["245127"] and "245127" not in [c.source_id for c in found.missing]
    mirror.Mirror(source, library, settings, workers=1, with_covers=False).run(found.unread)
    after = mirror.plan(source, library)
    assert not after.unread and [sid for sid, _ in after.no_cover] == ["245127"]  # read, but no cover saved (--no-covers)
    result = mirror.Mirror(source, library, settings, workers=1).run([], after.no_cover)
    assert result.covers == 1 and not mirror.plan(source, library).no_cover


def test_upgrading_from_v10_keeps_a_copy_and_compresses_the_pages_stored_before(tmp_path, capsys):
    import glob
    import sqlite3

    from bdnex.library import SCHEMA_VERSION
    from bdnex.library.schema import MIGRATIONS

    path = tmp_path / "lib.sqlite"
    html = b"<html>" + b"<p>Les 40 commandements</p>" * 800 + b"</html>"
    conn = sqlite3.connect(path)  # a library as bdnex v10 left it: the pages are plain
    for number, script in enumerate(MIGRATIONS[:10], start=1):
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 10")
    for n in range(5):
        conn.execute("INSERT INTO source_cache(source, kind, url, content) VALUES ('bedetheque', 'album', ?, ?)", (f"u{n}", html))
    conn.execute("INSERT INTO source_cache(source, kind, url, content) VALUES ('bedetheque', 'sitemap', 's', ?)", (b"<urlset/>",))
    conn.commit()
    conn.close()
    with Library(path) as library:
        assert library.schema_version == SCHEMA_VERSION
        stored = library.conn.execute("SELECT LENGTH(content) FROM source_cache WHERE kind = 'album'").fetchall()
        assert all(size < len(html) / 5 for (size,) in stored) and library.cache_get("bedetheque", "album", "u3") == html
        assert library.conn.execute("SELECT content FROM source_cache WHERE kind = 'sitemap'").fetchone()[0] == b"<urlset/>"
    assert glob.glob(f"{path}.bak-v10")  # the copy the upgrade keeps (plain pages inside)
    assert "compressing the 5 stored pages" in capsys.readouterr().err


def test_the_mirror_options_need_mirror_and_a_script_must_say_yes(world, capsys, monkeypatch):
    import sys

    with pytest.raises(SystemExit):
        init_cmd.run(["--limit", "5"], out=io.StringIO())
    assert "only for `bdnex init --mirror`" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # no terminal to ask on
    with pytest.raises(SystemExit):
        init_cmd.run(["--mirror", "--skip-list"], out=io.StringIO())
    assert "add --yes" in capsys.readouterr().err


def test_the_old_names_still_work_and_say_what_they_became():
    from bdnex.ui.commands import RENAMED

    assert RENAMED["fields"](["x"]) == ["ls", "--fields", "x"]
    assert RENAMED["mirror"](["-n", "--limit", "5"])[:2] == ["init", "--mirror"] and "--yes" in RENAMED["mirror"]([])


# ---- the parsed album list is kept in the library
def test_the_parsed_album_list_is_kept_and_read_back_without_parsing_the_sitemaps(world, monkeypatch, tmp_path):
    from bdnex.importer.bedetheque import sitemap

    web, library, source, settings = world
    first = list(source.catalogue())  # parsed, then stored
    assert first and library.cache_get("bedetheque", "index", "sitemaps") is not None
    again = Bedetheque(library, fetch=web, sleep=lambda s: None)
    monkeypatch.setattr(sitemap, "parse_sitemap", lambda content: (_ for _ in ()).throw(AssertionError("the sitemaps were parsed again")))
    assert again.catalogue() == first  # the same candidates: address, name, volume


def test_a_sitemap_fetched_again_makes_the_stored_list_stale_and_a_damaged_one_is_ignored(world):
    web, library, source, settings = world
    first = list(source.catalogue())
    stored = library.cache_get("bedetheque", "index", "sitemaps")
    library.conn.execute("UPDATE source_cache SET content = content || x'20' WHERE kind = 'sitemap' AND rowid = (SELECT MIN(rowid) FROM source_cache WHERE kind = 'sitemap')")
    library.conn.commit()  # another size: the signature changes
    assert Bedetheque(library, fetch=web, sleep=lambda s: None).catalogue() == first  # parsed again, same albums
    assert library.cache_get("bedetheque", "index", "sitemaps") != stored  # and stored again for the new sitemaps
    library.cache_put("bedetheque", "index", "sitemaps", b"not a stored list")
    assert Bedetheque(library, fetch=web, sleep=lambda s: None).catalogue() == first


def test_a_read_only_library_reads_the_list_and_keeps_nothing(world, tmp_path):
    web, library, source, settings = world
    library.conn.execute("DELETE FROM source_cache WHERE kind = 'index'")
    library.conn.commit()
    library.close()
    with Library(tmp_path / "lib.sqlite", readonly=True) as reader:
        assert len(Bedetheque(reader, fetch=web, sleep=lambda s: None).catalogue()) > 1  # no error, only no speed-up
