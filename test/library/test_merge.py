import pytest

from bdnex.library import AlbumInfo, Library, SeriesInfo


@pytest.fixture
def lib():
    with Library(":memory:") as library:
        yield library


def album(**kw):
    base = dict(source="s", source_id="1", title="T", publisher="Dargaud", year=2021, language="fr",
                credits={"Writer": ["W"]}, genres=["Humour"], extra={"a": 1})
    base.update(kw)
    return AlbumInfo(**base)


def test_a_fresh_parse_overrides_the_values_it_has(lib):
    album_id = lib.add_album(album())
    lib.merge_album(album(title="New title", year=2022, pages=48))
    stored = lib.get_album(album_id)
    assert (stored["title"], stored["year"], stored["pages"]) == ("New title", 2022, 48)


def test_values_it_lacks_are_never_erased(lib):
    album_id = lib.add_album(album(isbn="978", pages=56, summary="Résumé"))
    lib.merge_album(AlbumInfo(source="s", source_id="1", title="T"))  # a parse that found almost nothing
    stored = lib.get_album(album_id)
    assert (stored["isbn"], stored["pages"], stored["summary"], stored["publisher"], stored["year"]) == \
        ("978", 56, "Résumé", "Dargaud", 2021)
    assert stored["credits"] == {"Writer": ["W"]} and stored["genres"] == ["Humour"] and stored["extra"] == {"a": 1}


def test_a_credit_typed_at_a_prompt_survives(lib):
    album_id = lib.add_album(album())
    lib.add_credit(album_id, "Colorist", "Typed By Hand")
    lib.merge_album(album(credits={"Writer": ["W2"]}))
    assert lib.get_album(album_id)["credits"] == {"Colorist": ["Typed By Hand"], "Writer": ["W2"]}  # role replaced only


def test_new_roles_and_details_are_added(lib):
    album_id = lib.add_album(album())
    lib.merge_album(album(credits={"Translator": ["Maubille"]}, extra={"edition_info": "Première", "a": 2}))
    stored = lib.get_album(album_id)
    assert stored["credits"] == {"Translator": ["Maubille"], "Writer": ["W"]}
    assert stored["extra"] == {"a": 2, "edition_info": "Première"}


def test_a_full_parse_replaces_a_sitemap_only_record(lib):
    album_id = lib.add_album(album(extra={"partial": True}, series=SeriesInfo("s", "slug:x", "X"), volume=1))
    lib.merge_album(album(series=SeriesInfo("s", "47", "X"), volume=1, extra={}))
    stored = lib.get_album(album_id)
    assert "partial" not in stored["extra"] and stored["series"] == "X"
    lib.prune_orphan_series()
    assert lib.stats()["series"] == 1  # the placeholder series is gone, the real one stays


def test_a_partial_parse_stays_partial(lib):
    album_id = lib.add_album(album())
    lib.merge_album(album(extra={"partial": True}))
    assert lib.get_album(album_id)["extra"]["partial"] is True


def test_series_is_kept_when_the_parse_has_none(lib):
    album_id = lib.add_album(album(series=SeriesInfo("s", "47", "Nains"), volume=2))
    lib.merge_album(album())  # no series, no volume in this parse
    stored = lib.get_album(album_id)
    assert stored["series"] == "Nains" and stored["volume"] == 2 and stored["kind"] == "series"


def test_merging_an_unknown_album_adds_it(lib):
    assert lib.merge_album(album(source_id="9")) == lib.find_album("s", "9")
    assert lib.preview_merge(album(source_id="10")) == ["new album"]


def test_preview_lists_changes_and_changes_nothing(lib):
    album_id = lib.add_album(album())
    before = lib.get_album(album_id)
    changes = lib.preview_merge(album(title="New", credits={"Writer": ["W"], "Translator": ["Tr"]},
                                      genres=["Aventure"], extra={"edition_info": "x"}))
    assert "title: 'T' -> 'New'" in changes and "Translator: - -> Tr" in changes
    assert "genres: Humour -> Aventure" in changes and "new details: edition_info" in changes
    assert lib.get_album(album_id) == before


def test_preview_of_an_identical_parse_is_empty(lib):
    lib.add_album(album())
    assert lib.preview_merge(album()) == []


def test_merge_is_idempotent(lib):
    album_id = lib.add_album(album())
    info = album(credits={"Translator": ["Tr"]}, extra={"edition_info": "x"})
    lib.merge_album(info)
    first = lib.get_album(album_id)
    assert lib.preview_merge(info) == []
    lib.merge_album(info)
    assert lib.get_album(album_id) == first
