"""Finding files by folder, by planned folder, by facet: one join each, indexed."""

import pytest

from bdnex.config import build_settings
from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library import browse
from bdnex.library.review_batch import CHECKED


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "comics"
    root.mkdir()
    settings = build_settings({"config_version": 2, "collection_path": str(root), "library": str(tmp_path / "l.sqlite"),
                               "paths": {"series": "$category/$series/$series - T$volume:02 - $title"}})[0]
    lib = Library.for_settings(settings)
    nains, naruto = SeriesInfo("b", "n", "Nains"), SeriesInfo("b", "m", "Naruto")
    a1 = lib.add_album(AlbumInfo("b", "1", "Redwin", series=nains, volume=1, language="fr", category="BD", collection="Soleil", publisher="Soleil",
                                 genres=["Fantasy"], credits={"Writer": ["Jarry"]}))
    a2 = lib.add_album(AlbumInfo("b", "2", "Forge", series=nains, volume=2, language="fr", category="BD", genres=["Fantasy", "Humour"]))
    a3 = lib.add_album(AlbumInfo("b", "3", "Naruto 1", series=naruto, volume=1, category="Manga"))
    shot = lib.add_album(AlbumInfo("b", "4", "Maus"))
    files = {}
    for name, album, spare in (("Series/Nains/1.cbz", a1, 0), ("Series/Nains/2.cbz", a2, 0), ("Series/Nains/2 bis.cbz", a2, 1),
                               ("Manga/Naruto/1.cbz", a3, 0), ("OneShot/Maus.cbz", shot, 0), ("OneShot/loose.cbz", None, 0)):
        with lib.conn:
            files[name] = lib.conn.execute("INSERT INTO files(path, album_id, md5, size, format, spare) VALUES (?, ?, 'm', 1, 'cbz', ?)",
                                           (name, album, spare)).lastrowid
    lib.set_attr("file", files["Series/Nains/1.cbz"], CHECKED, "keep")
    with lib.conn:
        lib.conn.execute("INSERT INTO import_log(path, outcome, score, album_id) VALUES ('Series/Nains/1.cbz', 'tagged', 95.0, ?)", (a1,))
    lib.set_relation(shot, a1, "version")
    with lib.conn:
        lib.conn.execute("UPDATE files SET review = 'doubtful' WHERE id = ?", (files["Manga/Naruto/1.cbz"],))
    return settings, lib, files


def names(batch):
    return [r.path.split("comics/", 1)[1] for r in batch]


def test_a_folder_gives_everything_below_it_and_nothing_of_a_sibling(env):
    settings, lib, files = env
    assert names(browse.rows_for(lib, settings, "folder", "Series")) == ["Series/Nains/1.cbz", "Series/Nains/2 bis.cbz", "Series/Nains/2.cbz"]
    assert names(browse.rows_for(lib, settings, "folder", "Series/Nains")) == ["Series/Nains/1.cbz", "Series/Nains/2 bis.cbz", "Series/Nains/2.cbz"]
    assert len(browse.rows_for(lib, settings, "folder", "")) == 6
    assert browse.rows_for(lib, settings, "folder", "Series/Nai") == []  # a folder is not a prefix of a name
    got = browse.rows_for(lib, settings, "folder", "Series/Nains", limit=2)
    assert len(got) == 2 and got.total == 3


def test_the_rows_say_what_each_file_is(env):
    settings, lib, files = env
    first, bis, second = browse.rows_for(lib, settings, "folder", "Series/Nains")
    assert first.album == "Nains - T01 - Redwin" and (first.album_kind, first.volume, first.category) == ("series", 1, "BD")
    assert first.previous == "keep" and first.kind == "ok" and bis.spare and bis.album_id == second.album_id
    loose = browse.rows_for(lib, settings, "folder", "OneShot")
    assert sorted((r.album or "", r.kind) for r in loose) == [("", "unmatched"), ("Maus", "ok")]  # no score: you chose it (manual), nothing against
    manga = browse.rows_for(lib, settings, "folder", "Manga")[0]
    assert manga.kind == "unconfirmed" and manga.link == "doubtful" and first.link == "auto matched" and second.link == "manual"


@pytest.mark.parametrize("group,value,expected", [
    ("Colour", "green", 1), ("Colour", "cyan", 3), ("Colour", "orange", 0), ("Colour", "red", 2),
    ("Link", "auto matched", 1), ("Link", "medium match", 0), ("Link", "doubtful", 1), ("Link", "no album", 1), ("Link", "manual", 3),
    ("Review", "confirmed", 1), ("Review", "not confirmed", 5), ("Review", "no album online", 0),
    ("Other", "duplicates", 2), ("Other", "versions", 1), ("Other", "damaged", 0),
    ("Kind", "series", 4), ("Kind", "oneshot", 1), ("Category", "BD", 3),
    ("Category", browse.NONE, 1), ("Collection", "Soleil", 1), ("Genre", "Fantasy", 3), ("Genre", "Humour", 2), ("Author", "Jarry", 1),
    ("Series", "Nains", 3), ("Publisher", "Soleil", 1), ("Format", "cbz", 6), ("Language", "fr", 3)])
def test_a_facet_finds_its_files(env, group, value, expected):
    settings, lib, files = env
    got = browse.rows_for(lib, settings, "facet", (group, value))
    assert got.total == expected == len(got)


def test_the_facets_list_their_values_with_the_number_of_files(env):
    settings, lib, files = env
    found = browse.facets(lib)
    assert dict(found["Review"]) == {"confirmed": 1, "not confirmed": 5, "no album online": 0} and dict(found["Genre"]) == {"Fantasy": 3, "Humour": 2}
    assert dict(found["Colour"]) == {"green": 1, "cyan": 3, "orange": 0, "red": 2} and dict(found["Link"])["auto matched"] == 1
    assert dict(found["Category"]) == {"BD": 3, "Manga": 1, browse.NONE: 1} and dict(found["Series"]) == {"Nains": 3, "Naruto": 1}
    assert set(found) == set(browse.GROUPS)


def test_the_plan_says_where_a_move_would_put_each_file_and_its_files_can_be_listed(env):
    settings, lib, files = env
    planned = browse.plan(lib, settings)
    assert planned[files["Series/Nains/1.cbz"]] == "BD/Nains/Nains - T01 - Redwin.cbz"
    assert planned[files["Series/Nains/2 bis.cbz"]].startswith("_versions/BD/Nains/")  # a spare version
    assert files["OneShot/loose.cbz"] not in planned  # no album: nowhere to go
    levels = {file_id: level for file_id, _, level in browse.file_levels(lib, 90.0)}
    counts = browse.plan_counts(planned, levels)
    assert counts["BD/Nains"] == [2, 1, 0, 0, 1] and counts["_versions/BD/Nains"] == [1, 0, 0, 0, 1]  # files, green, red, orange, cyan
    got = browse.rows_for(lib, settings, "planned", "BD", planned)
    assert [r.dest for r in got] == ["BD/Nains/Nains - T01 - Redwin.cbz", "BD/Nains/Nains - T02 - Forge.cbz"] and got.total == 2
    assert browse.plan(lib, settings, only={lib.get_file(settings.require_collection() / "Manga/Naruto/1.cbz")["album_id"]}).keys() == {files["Manga/Naruto/1.cbz"]}


def test_several_selections_are_alternatives_inside_a_kind_and_go_together_across_kinds(env):
    settings, lib, files = env
    both = browse.rows_for_many(lib, settings, [("folder", "Series/Nains"), ("folder", "Manga")])
    assert both.total == 4 and len(both) == 4  # two folders: the files of either
    genres = browse.rows_for_many(lib, settings, [("facet", ("Genre", "Fantasy")), ("facet", ("Genre", "Humour"))])
    assert genres.total == 3  # one group: either value
    narrowed = browse.rows_for_many(lib, settings, [("folder", "Series"), ("facet", ("Review", "not confirmed"))])
    assert narrowed.total == 2  # the folder AND the state: Nains 2 and its bis (1.cbz is reviewed)
    planned = browse.plan(lib, settings)
    mixed = browse.rows_for_many(lib, settings, [("planned", "BD"), ("facet", ("Review", "not confirmed"))], planned)
    assert [r.dest for r in mixed] == ["BD/Nains/Nains - T02 - Forge.cbz"] and mixed.total == 1


def test_a_plan_stops_when_asked_so_that_quitting_the_screen_does_not_wait_for_all_of_it(env):
    settings, lib, files = env
    asked = []

    def stop():
        asked.append(1)
        return True

    with pytest.raises(browse.Stopped):
        browse.plan(lib, settings, stop=stop)
    assert asked  # asked, and it stopped at once


def test_a_listed_file_shows_its_size_with_its_format_and_pages(env):
    settings, lib, files = env
    lib.conn.execute("UPDATE files SET size = 3 * 1024 * 1024, page_count = 413 WHERE id = ?", (files["Series/Nains/1.cbz"],))
    lib.conn.commit()
    rows = browse.rows_for(lib, settings, "folder", "Series/Nains")
    row = next(r for r in rows if r.file_id == files["Series/Nains/1.cbz"])
    assert "413 pages" in row.facts and "3.0 MiB" in row.facts
