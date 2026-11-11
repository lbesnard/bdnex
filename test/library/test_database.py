import sqlite3

import pytest

from bdnex.library import AlbumInfo, Library, SeriesInfo, SCHEMA_VERSION
from bdnex.library.database import SchemaTooNewError


@pytest.fixture
def lib():
    with Library(":memory:") as library:
        yield library


def nains(**kw):
    base = dict(
        source="bedetheque", source_id="245127", title="Redwin de la Forge",
        series=SeriesInfo("bedetheque", "47467", "Nains", summary="abstract"),
        volume=1, publisher="Soleil Productions", isbn="978-2-302-04644-3",
        year=2015, month=6, day=3, pages=56, language="fr", rating=4.25,
        credits={"Writer": ["Jarry, Nicolas"], "Penciller": ["Goux, Pierre-Denis"],
                 "Colorist": ["Digikore Studios"]},
        genres=["Fantasy"], extra={"Origine": "Europe"})
    base.update(kw)
    return AlbumInfo(**base)


def oneshot():
    return AlbumInfo(source="bedetheque", source_id="999", title="Seul au monde",
                     credits={"Writer": ["Doe, Jane"]})


def test_schema_created_and_versioned(lib):
    assert lib.schema_version == SCHEMA_VERSION
    tables = {r[0] for r in lib.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"files", "albums", "series", "people", "album_credits", "genres",
            "album_genres", "publishers", "source_cache", "import_log"} <= tables


def test_reopen_does_not_remigrate(tmp_path):
    path = tmp_path / "sub" / "lib.sqlite"
    Library(path).close()
    with Library(path) as again:
        assert again.schema_version == SCHEMA_VERSION


def test_newer_schema_refused(tmp_path):
    path = tmp_path / "lib.sqlite"
    Library(path).close()
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaTooNewError):
        Library(path)


def test_add_series_album_roundtrip(lib):
    album_id = lib.add_album(nains())
    album = lib.get_album(album_id)
    assert album["kind"] == "series"
    assert album["series"] == "Nains"
    assert album["publisher"] == "Soleil Productions"
    assert album["volume"] == 1 and album["year"] == 2015
    assert album["credits"]["Writer"] == ["Jarry, Nicolas"]
    assert album["genres"] == ["Fantasy"]
    assert album["extra"] == {"Origine": "Europe"}


def test_oneshot_has_no_series(lib):
    album = lib.get_album(lib.add_album(oneshot()))
    assert album["kind"] == "oneshot"
    assert album["series"] is None


def test_kind_series_constraint_enforced(lib):
    with pytest.raises(sqlite3.IntegrityError):
        lib.conn.execute(
            "INSERT INTO albums(kind, title, source, source_id) VALUES ('series', 't', 's', '1')")


def test_add_album_is_idempotent_and_updates(lib):
    first = lib.add_album(nains())
    second = lib.add_album(nains(title="Redwin", credits={"Writer": ["Someone Else"]}, genres=[]))
    assert first == second
    album = lib.get_album(first)
    assert album["title"] == "Redwin"
    assert album["credits"] == {"Writer": ["Someone Else"]}
    assert album["genres"] == []
    assert lib.stats()["albums"] == 1


def test_shared_people_and_series_are_deduplicated(lib):
    lib.add_album(nains())
    lib.add_album(nains(source_id="245128", title="Tome 2", volume=2))
    assert lib.stats()["series"] == 1
    assert lib.stats()["people"] == 3


def test_find_album(lib):
    album_id = lib.add_album(oneshot())
    assert lib.find_album("bedetheque", "999") == album_id
    assert lib.find_album("bedetheque", "nope") is None


def test_cache_roundtrip_and_clear(lib):
    lib.cache_put("bedetheque", "album", "http://x/1", "héllo")
    assert lib.cache_get("bedetheque", "album", "http://x/1").decode() == "héllo"
    lib.cache_put("bedetheque", "album", "http://x/1", b"new")
    assert lib.cache_get("bedetheque", "album", "http://x/1") == b"new"
    lib.cache_put("bedetheque", "series", "http://x/s", b"s")
    assert lib.cache_clear("bedetheque", "album") == 1
    assert lib.cache_get("bedetheque", "album", "http://x/1") is None
    assert lib.cache_get("bedetheque", "series", "http://x/s") == b"s"
    assert lib.cache_clear("bedetheque") == 1


def test_import_log(lib):
    lib.log_import("/tmp/a.cbz", "skipped", score=0.1)
    lib.log_import("/tmp/a.cbz", "tagged", score=0.9)
    last = lib.last_import("/tmp/a.cbz")
    assert last["outcome"] == "tagged" and last["score"] == 0.9
    assert lib.last_import("/tmp/none.cbz") is None


def test_deleting_album_keeps_file(lib, make_cbz):
    album_id = lib.add_album(oneshot())
    path = make_cbz()
    lib.add_file(path, album_id)
    with lib.conn:
        lib.conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    assert lib.get_file(path)["album_id"] is None
