"""One form for many files: what they share, the fields set on all, the series or one-shot, a volume each."""

import pytest

from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library import bulk
from bdnex.library.bulk import Target
from bdnex.library.review_batch import CHECKED


@pytest.fixture
def lib(tmp_path):
    return Library(":memory:", root=tmp_path)


def make(lib, make_cbz, n, series="Tintin", **kw):
    album = lib.add_album(AlbumInfo("b", str(n), f"Album {n}", series=SeriesInfo("b", f"s-{series}", series) if series else None,
                                    volume=n if series else None, publisher="Casterman", **kw))
    path = make_cbz(f"{n}.cbz")
    lib.add_file(path, album)
    return Target(path, album)


def test_shared_gives_the_common_value_and_nothing_where_they_differ(lib, make_cbz):
    a, b = make(lib, make_cbz, 1, collection="Hergé"), make(lib, make_cbz, 2, collection="Hergé")
    got = bulk.shared(lib, [a.album_id, b.album_id])
    assert got["collection"] == "Hergé" and got["series"] == "Tintin" and got["publisher"] == "Casterman"
    assert got["volume"] is None and got["title"] is None and got["kind"] == "series"
    lib.edit_album(b.album_id, {"collection": None, "publisher": "Dupuis"})
    got = bulk.shared(lib, [a.album_id, b.album_id])
    assert got["collection"] is None and got["publisher"] is None


def test_the_fields_changed_are_set_on_every_album_and_the_files_are_marked_reviewed(lib, make_cbz):
    targets = [make(lib, make_cbz, n) for n in (1, 2, 3)]
    lib.flag_review(targets[0].path, "doubtful")
    out = bulk.apply(lib, targets, {"collection": "Aventures", "category": "BD", "genres": "Aventure; Jeunesse", "writer": "Hergé"})
    assert out.ok and out.changed == 3 and out.marked == 3
    for target in targets:
        album = lib.get_album(target.album_id)
        assert (album["collection"], album["category"], album["genres"]) == ("Aventures", "BD", ["Aventure", "Jeunesse"])
        assert album["credits"]["Writer"] == ["Hergé"] and album["publisher"] == "Casterman"  # not asked: left alone
        assert lib.attrs_of("file", lib.get_file(target.path)["id"])[CHECKED] == "keep"
    assert lib.get_file(targets[0].path)["review"] is None
    assert bulk.apply(lib, targets, {"collection": "Aventures"}).changed == 0  # nothing differs: nothing changes


def test_volumes_are_set_per_file(lib, make_cbz):
    targets = [make(lib, make_cbz, n) for n in (1, 2)]
    out = bulk.apply(lib, targets, {}, volumes={targets[0].path: 11, targets[1].path: None})
    assert out.ok and [lib.get_album(t.album_id)["volume"] for t in targets] == [11, None]


def test_a_bad_value_writes_nothing(lib, make_cbz):
    targets = [make(lib, make_cbz, n) for n in (1, 2)]
    out = bulk.apply(lib, targets, {"collection": "Mine", "year": "twenty"})
    assert not out.ok and out.changed == 0
    assert lib.get_album(targets[0].album_id)["collection"] is None  # the valid field was not written either
    assert bulk.apply(lib, targets, {}, volumes={targets[0].path: 1, targets[1].path: 1}).ok
    assert not bulk.apply(lib, targets, {}, kind="series").ok  # a series needs its name


def test_albums_join_one_series_or_become_one_shots(lib, make_cbz):
    targets = [make(lib, make_cbz, n, series=series) for n, series in ((1, "Tintin"), (2, "Tintin - Divers"), (3, None))]
    out = bulk.apply(lib, targets, {"series": "Les Aventures de Tintin"}, kind="series")
    assert out.ok and out.changed == 3
    assert {lib.get_album(t.album_id)["series"] for t in targets} == {"Les Aventures de Tintin"}
    assert len({lib.series_id_of(t.album_id) for t in targets}) == 1 and lib.get_album(targets[2].album_id)["kind"] == "series"
    out = bulk.apply(lib, targets[:1], {}, kind="oneshot")
    assert out.ok and lib.get_album(targets[0].album_id)["kind"] == "oneshot"
    assert lib.get_album(targets[1].album_id)["series"] == "Les Aventures de Tintin"  # the others stayed


def test_a_file_without_album_gets_a_hand_written_one_when_it_is_asked_for_a_series(lib, make_cbz):
    path = make_cbz("loose.cbz")
    lib.add_file(path)
    target = Target(path)
    assert not bulk.apply(lib, [target], {"collection": "x"}).ok  # nothing to say what it is
    out = bulk.apply(lib, [target], {"series": "Klezmer", "collection": "Bayou"}, kind="series", volumes={path: 4})
    assert out.ok and out.created == 1
    album = lib.get_album(lib.get_file(path)["album_id"])
    assert (album["series"], album["volume"], album["collection"], album["source"]) == ("Klezmer", 4, "Bayou", "manual")


def test_a_hors_serie_is_a_number_less_tome_with_a_special_that_counts_up(lib, make_cbz):
    targets = [make(lib, make_cbz, n) for n in (1, 2, 3)]
    specials = {t.path: f"HS {i}" for i, t in enumerate(targets[1:], start=1)}
    out = bulk.apply(lib, targets, {}, volumes={**{t.path: None for t in targets[1:]}}, specials=specials)
    assert out.ok
    got = [lib.get_album(t.album_id) for t in targets]
    assert [(a["volume"], a["alternate_number"]) for a in got] == [(1, None), (None, "HS 1"), (None, "HS 2")]
    out = bulk.apply(lib, targets[1:2], {}, volumes={targets[1].path: 4}, specials={targets[1].path: None})  # back to a number
    assert out.ok and (lib.get_album(targets[1].album_id)["volume"], lib.get_album(targets[1].album_id)["alternate_number"]) == (4, None)


def test_a_dash_in_the_form_empties_a_collection_the_site_gives_on_every_album(tmp_path):
    from bdnex.library import AlbumInfo, Library, SeriesInfo, bulk
    from bdnex.organizer import template

    with Library(tmp_path / "lib.sqlite") as lib:
        ids = [lib.add_album(AlbumInfo("b", f"x{n}", f"Album {n}", series=SeriesInfo("b", "s", "S"), volume=n)) for n in (1, 2)]
        with lib.conn:
            lib.conn.execute("UPDATE albums SET extra = ? WHERE id = ?", ('{"edition_fields": {"Collection": "Pilote"}}', ids[0]))
        lib.edit_album(ids[1], {"collection": "Other"})
        assert bulk.shared(lib, ids)["collection"] is None  # they differ: the form says "(several values)"
        out = bulk.apply(lib, [bulk.Target(f"/x/{n}.cbz", album_id=i) for n, i in enumerate(ids)], {"collection": ""}, mark=False)
        assert out.ok and out.changed == 2
        assert [template.fields_for(lib.get_album(i))["collection"] for i in ids] == ["", ""]  # none on both, whatever they had
        assert bulk.shared(lib, ids)["collection"] == ""
