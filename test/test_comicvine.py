"""Comic Vine by address: the address, the mapping of an issue to an album, a volume read for several files, the key and Simyan's setup.

Nothing here calls Comic Vine: Simyan's client is replaced by one that answers from memory.
"""

import datetime
from types import SimpleNamespace as NS

import pytest

from bdnex.config import build_settings
from bdnex.importer import comicvine


def issue(id_=111, number="1", name="Feral City", volume_id=18040, **extra):
    base = dict(id=id_, number=number, name=name, cover_date=datetime.date(2005, 7, 1), store_date=datetime.date(2005, 6, 15),
                description="<p>Richard Fell.</p><p>A detective.</p>", summary="deck", image=NS(original_url="https://x/c.jpg"),
                site_url=f"https://comicvine.gamespot.com/fell-1/4000-{id_}/", volume=NS(id=volume_id, name="Fell"),
                creators=[NS(name="Warren Ellis", roles="writer"), NS(name="Ben Templesmith", roles="penciler, inker, cover"),
                          NS(name="Jim Doe", roles="production")],
                characters=[NS(name="Richard Fell")], teams=[], locations=[NS(name="Snowtown")], story_arcs=[])
    base.update(extra)
    return NS(**base)


def volume(count=9, issues=None):
    return NS(id=18040, name="Fell", issue_count=count, start_year=2005, publisher=NS(name="Image"), summary="A detective story",
              description=None, site_url="https://comicvine.gamespot.com/fell/4050-18040/",
              issues=issues if issues is not None else [NS(id=100 + n, number=str(n), name=f"Issue {n}") for n in range(1, 10)])


class FakeClient:
    def __init__(self, issues=None, vol=None, fail_after=None):
        self.issues = issues or {}
        self.vol = vol or volume()
        self.calls, self.fail_after = [], fail_after

    def get_volume(self, id_):
        self.calls.append(("volume", id_))
        return self.vol

    def get_issue(self, id_):
        self.calls.append(("issue", id_))
        if self.fail_after is not None and len([c for c in self.calls if c[0] == "issue"]) > self.fail_after:
            raise type("RateLimitError", (Exception,), {})("slow down")
        return self.issues.get(id_) or issue(id_, number=str(id_ - 100), name=None)


def settings(tmp_path, **comicvine_options):
    return build_settings({"config_version": 2, "library": str(tmp_path / "lib" / "l.sqlite"),
                           "comicvine": {"api_key": "k", **comicvine_options}})[0]


@pytest.mark.parametrize("text,expected", [
    ("https://comicvine.gamespot.com/fell/4050-18040/", ("volume", 18040)),
    ("https://comicvine.gamespot.com/fell-1-feral-city/4000-123456/", ("issue", 123456)),
    ("https://comicvine.gamespot.com/4050-18040/", ("volume", 18040)),
    ("  4000-77 ", ("issue", 77)),
    ("https://comicvine.gamespot.com/fell/4050-18040/#characters", ("volume", 18040)),
    ("https://www.bedetheque.com/serie-1-BD-Fell.html", None),
    ("https://www.bdtheque.com/series/14922/x", None),
    ("1234", None), ("", None)])
def test_the_address_of_an_issue_or_a_volume(text, expected):
    assert comicvine.parse(text) == expected and comicvine.is_address(text) == (expected is not None)


def test_an_issue_of_a_volume_becomes_an_album_of_a_series():
    info = comicvine.album_info(issue(), volume())
    assert (info.source, info.source_id, info.title, info.volume, info.language) == ("comicvine", "111", "Feral City", 1, "en")
    assert info.series.source_id == "18040" and info.series.name == "Fell" and info.publisher == "Image"
    assert (info.year, info.month, info.day) == (2005, 7, 1)  # the cover date, not the store date
    assert info.summary == "Richard Fell.\n\nA detective." and info.cover_url == "https://x/c.jpg"
    assert info.credits == {"Writer": ["Warren Ellis"], "Penciller": ["Ben Templesmith"], "Inker": ["Ben Templesmith"], "CoverArtist": ["Ben Templesmith"]}
    assert info.extra["characters"] == ["Richard Fell"] and info.extra["other_roles"] == ["production"] and "teams" not in info.extra


def test_an_issue_with_no_name_and_a_special_number():
    unnamed = comicvine.album_info(issue(name=None), volume())
    assert unnamed.title == "#1"
    annual = comicvine.album_info(issue(number="Annual 1", name="Fell Annual"), volume())
    assert annual.volume is None and annual.alternate_number == "Annual 1"


def test_a_volume_of_one_issue_is_a_one_shot():
    info = comicvine.album_info(issue(number="1", name=None), volume(count=1))
    assert info.series is None and info.volume is None and info.title == "Fell"


def test_reading_an_issue_needs_its_volume_too(tmp_path, monkeypatch):
    fake = FakeClient(issues={111: issue()})
    monkeypatch.setattr(comicvine, "client", lambda settings: fake)
    info = comicvine.read_issue(settings(tmp_path), "https://comicvine.gamespot.com/fell-1/4000-111/")
    assert info.series.name == "Fell" and fake.calls == [("issue", 111), ("volume", 18040)]
    with pytest.raises(comicvine.ComicvineError, match="not the address of a Comic Vine issue"):
        comicvine.read_issue(settings(tmp_path), "https://comicvine.gamespot.com/fell/4050-18040/")


def test_a_volume_gives_each_file_the_issue_with_the_number_in_its_name(tmp_path, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(comicvine, "client", lambda settings: fake)
    paths = ["/c/Fell/Fell T02.cbz", "/c/Fell/Fell T05.cbz", "/c/Fell/Fell T42.cbz", "/c/Fell/no number.cbz"]
    answer = comicvine.read_volume(settings(tmp_path), "https://comicvine.gamespot.com/fell/4050-18040/", paths)
    assert sorted(answer.mapping) == ["/c/Fell/Fell T02.cbz", "/c/Fell/Fell T05.cbz"]
    assert answer.mapping["/c/Fell/Fell T05.cbz"].volume == 5 and answer.mapping["/c/Fell/Fell T05.cbz"].series.name == "Fell"
    assert sorted(answer.unmatched) == ["/c/Fell/Fell T42.cbz", "/c/Fell/no number.cbz"] and answer.stopped is None
    assert fake.calls == [("volume", 18040), ("issue", 102), ("issue", 105)]  # one request for the volume, one per file that matched


def test_the_limit_stops_the_reading_and_keeps_the_files_done(tmp_path, monkeypatch):
    fake = FakeClient(fail_after=1)
    monkeypatch.setattr(comicvine, "client", lambda settings: fake)
    paths = [f"/c/Fell/Fell T0{n}.cbz" for n in (1, 2, 3)]
    answer = comicvine.read_volume(settings(tmp_path), "https://comicvine.gamespot.com/fell/4050-18040/", paths)
    assert list(answer.mapping) == [paths[0]] and "limit" in answer.stopped and answer.unmatched == paths[1:]


def test_no_key_and_no_simyan_are_said_in_a_sentence(tmp_path, monkeypatch):
    monkeypatch.delenv(comicvine.KEY_ENV, raising=False)
    with pytest.raises(comicvine.ComicvineError, match="no Comic Vine API key.*comicvine.api_key"):
        comicvine.client(build_settings({"config_version": 2, "library": str(tmp_path / "l.sqlite")})[0])
    monkeypatch.setenv(comicvine.KEY_ENV, "from-env")
    assert comicvine.api_key(settings(tmp_path)) == "from-env"  # the environment wins over the file
    monkeypatch.setitem(__import__("sys").modules, "simyan.comicvine", None)  # `import simyan.comicvine` now fails
    with pytest.raises(comicvine.ComicvineError, match=r"bdnex\[comicvine\]"):
        comicvine.client(settings(tmp_path))


def test_the_client_keeps_its_cache_and_limit_counters_next_to_the_library(tmp_path):
    pytest.importorskip("simyan")
    client = comicvine.client(settings(tmp_path, cache_days=3, timeout=7))
    assert client._timeout == 7
    assert (tmp_path / "lib").is_dir()  # the folder of the library: a volume in Docker
    assert not (tmp_path / "lib" / "comicvine-cache.sqlite").is_dir()


def test_the_key_is_a_secret():
    from bdnex.config import SECRET_KEYS, masked

    assert "comicvine.api_key" in SECRET_KEYS
    assert masked({"comicvine": {"api_key": "abc"}})["comicvine"]["api_key"] == "<hidden>"
