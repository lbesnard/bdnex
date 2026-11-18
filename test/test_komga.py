"""``bdnex komga``: matching Komga books to library files, and sending only what differs. Komga is a fake."""

import io

from bdnex import komga
from bdnex.config import Komga
from bdnex.ui import komga_cmd


def file(path, size, album=1):
    return {"path": path, "size": size, "id": album}


def book(id_, url, size, series="s1", metadata=None):
    return {"id": id_, "url": url, "sizeBytes": size, "seriesId": series, "metadata": metadata or {}}


def test_match_by_name_and_size_ignores_the_folders():
    out = komga.match([file("/home/me/Comics/A/Nains T01.pdf", 10)], [book("b1", "/data/x/Nains T01.pdf", 10)])
    assert [(f["path"], b["id"], how) for f, b, how in out["pairs"]] == [
        ("/home/me/Comics/A/Nains T01.pdf", "b1", "name and size")]


def test_a_changed_size_still_matches_a_name_that_is_unique():
    out = komga.match([file("/c/a.pdf", 10)], [book("b1", "/k/a.pdf", 99)])
    assert out["pairs"][0][2] == "name"


def test_a_name_twice_is_resolved_by_folders_or_left_alone():
    books = [book("b1", "/k/Nains/T01.cbz", 5), book("b2", "/k/Elfes/T01.cbz", 5)]
    out = komga.match([file("/c/Elfes/T01.cbz", 5)], books)
    assert out["pairs"][0][1]["id"] == "b2"
    out = komga.match([file("/c/Other/T01.cbz", 5)], books)
    assert out["ambiguous"] and not out["pairs"]


def test_a_file_komga_does_not_know_is_counted():
    out = komga.match([file("/c/a.cbz", 1)], [])
    assert len(out["no_book"]) == 1


ALBUM = {"id": 1, "title": "Redwin", "series": "Nains", "volume": 1, "summary": "Un nain.", "year": 2010,
         "month": 5, "day": None, "isbn": "123", "url": "https://bdt/x", "publisher": "Soleil", "language": "fr",
         "credits": {"Writer": ["Nicolas Jarry"], "CoverArtist": ["Obion"]}, "genres": ["Fantasy"]}


def test_book_fields_use_komga_names():
    fields = komga.book_fields(ALBUM)
    assert fields["releaseDate"] == "2010-05-01" and fields["numberSort"] == 1.0
    assert fields["authors"] == [{"name": "Nicolas Jarry", "role": "writer"}, {"name": "Obion", "role": "cover"}]


def test_nothing_differs_nothing_is_sent_and_a_missing_lock_is_set_once():
    fields = {"title": "T", "summary": "S"}
    locked = {"title": "T", "summary": "S", "titleLock": True, "summaryLock": True}
    assert komga.changes(fields, locked, komga.BOOK_LOCKS, True) == ({}, [])
    payload, differing = komga.changes(fields, {"title": "T", "summary": "S"}, komga.BOOK_LOCKS, True)
    assert differing == [] and payload["titleLock"] is True
    payload, differing = komga.changes(fields, {"title": "Old", "summary": "S"}, komga.BOOK_LOCKS, False)
    assert payload == {"title": "T"} and differing == ["title"]


def test_authors_are_compared_without_order_or_case_of_the_role():
    mine = [{"name": "A", "role": "writer"}, {"name": "B", "role": "cover"}]
    theirs = [{"name": "B", "role": "COVER"}, {"name": "A", "role": "WRITER"}]
    assert komga.changes({"authors": mine}, {"authors": theirs, "authorsLock": True}, komga.BOOK_LOCKS, True)[0] == {}


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, str(data)
        self.content = b"x" if data is not None else b""

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, books, series_meta=None):
        self.headers, self.auth, self.calls = {}, None, []
        self.books, self.series_meta, self.no_list = books, series_meta or {}, False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url.split("//", 1)[1].split("/", 1)[1], kwargs.get("json")))
        if method == "POST" and url.endswith("/api/v1/books/list"):
            if self.no_list:
                return FakeResponse("not found", 404)
            return FakeResponse({"content": self.books, "last": True})
        if method == "GET" and url.endswith("/api/v1/books"):
            return FakeResponse({"content": self.books, "last": True})
        if method == "GET":
            return FakeResponse({"metadata": self.series_meta})
        return FakeResponse(None)




def test_the_client_needs_an_address_and_a_login():
    import pytest
    with pytest.raises(komga.KomgaError, match="komga.url"):
        komga.Client(Komga(url=None, api_key=None, username=None, password=None, lock=True, timeout=5))
    with pytest.raises(komga.KomgaError, match="login"):
        komga.Client(Komga(url="http://k", api_key=None, username=None, password=None, lock=True, timeout=5))
    session = FakeSession([])
    komga.Client(Komga(url="http://k", api_key="KEY", username=None, password=None, lock=True, timeout=5), session)
    assert session.headers["X-API-Key"] == "KEY"


def test_the_plan_sends_one_patch_per_changed_book_and_series():
    session = FakeSession([], series_meta={"title": "Nains"})
    client = komga.Client(Komga(url="http://k", api_key="K", username=None, password=None, lock=True, timeout=5),
                          session)
    args = type("A", (), {"only": None, "wanted": (None, None)})()
    plan = komga_cmd._plan(client, [(file("/c/a.pdf", 1), book("b1", "/k/a.pdf", 1), "name and size")],
                           {1: ALBUM}, args, True)
    assert [s["kind"] for s in plan] == ["book", "series"]
    out = io.StringIO()
    assert komga_cmd._send(client, [s for s in plan if s["payload"]], out, False) == 0
    methods = [(m, p) for m, p, _ in session.calls if m == "PATCH"]
    assert methods == [("PATCH", "api/v1/books/b1/metadata"), ("PATCH", "api/v1/series/s1/metadata")]


# ------------------------------------------------------------------- the command, on a real library file
import pytest  # noqa: E402
import yaml  # noqa: E402

from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402


@pytest.fixture
def cmd_env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db),
                                    "komga": {"url": "http://k:25600", "api_key": "KEY"}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    path = make_cbz("Nains T01.cbz")
    with Library(db) as lib:
        album = lib.add_album(AlbumInfo("b", "1", "Redwin", series=SeriesInfo("b", "s", "Nains"), volume=1,
                                        publisher="Soleil", credits={"Writer": ["Jarry"]}, genres=["Fantasy"]))
        lib.add_file(path, album)
    import os
    return {"url": "/komga/Nains T01.cbz", "size": os.path.getsize(path)}


class Answer:
    def __init__(self, value=True):
        self.value, self.asked = value, []

    def confirm(self, message):
        self.asked.append(message)
        return self.value


def send(argv, cmd_env, answer=True):
    session = FakeSession([book("b1", cmd_env["url"], cmd_env["size"])], series_meta={"title": "Old"})
    out, prompter = io.StringIO(), Answer(answer)
    code = komga_cmd.run(argv, out=out, prompter=prompter, session=session)
    return code, out.getvalue(), session, prompter


def test_pretend_shows_the_changes_and_sends_nothing(cmd_env):
    code, text, session, prompter = send(["push", "-n"], cmd_env)
    assert code == 0 and "Redwin" in text and "1 book(s) and 1 series" in text
    assert not [c for c in session.calls if c[0] == "PATCH"] and not prompter.asked


def test_a_real_run_asks_then_patches_book_and_series(cmd_env):
    code, text, session, prompter = send(["push"], cmd_env)
    assert code == 0 and prompter.asked
    patches = {p: body for m, p, body in session.calls if m == "PATCH"}
    assert patches["api/v1/books/b1/metadata"]["title"] == "Redwin"
    assert patches["api/v1/books/b1/metadata"]["titleLock"] is True
    assert patches["api/v1/series/s1/metadata"]["title"] == "Nains"


def test_declining_sends_nothing_and_only_books_skips_the_series(cmd_env):
    code, _, session, _ = send(["push"], cmd_env, answer=False)
    assert code == 1 and not [c for c in session.calls if c[0] == "PATCH"]
    _, _, session, _ = send(["push", "-y", "--only", "books"], cmd_env)
    assert [p for m, p, _ in session.calls if m == "PATCH"] == ["api/v1/books/b1/metadata"]


def test_a_query_filters_the_files(cmd_env):
    code, text, session, _ = send(["push", "-y", "author:nobody"], cmd_env)
    assert code == 0 and not [c for c in session.calls if c[0] == "PATCH"]


def test_no_komga_address_is_explained(cmd_env, monkeypatch, tmp_path, capsys):
    conf = tmp_path / "other.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite")}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    assert komga_cmd.run(["push", "-n"]) == 1
    assert "komga.url" in capsys.readouterr().err


def test_show_prints_what_komga_holds_next_to_the_library_and_changes_nothing(cmd_env):
    code, text, session, prompter = send(["show"], cmd_env)
    assert code == 0 and "1 in Komga" in text and "Nains T01.cbz" in text
    assert "| title " in text and "Redwin" in text and "push adds it" in text
    assert [m for m, p, _ in session.calls if m == "PATCH"] == [] and not prompter.asked


def test_show_with_v_adds_the_series_and_json_is_machine_readable(cmd_env):
    _, text, session, _ = send(["show", "-v"], cmd_env)
    assert "| series title " in text and "Old" in text and "push changes it" in text
    code, text, _, _ = send(["show", "--json"], cmd_env)
    import json
    data = json.loads(text[text.index("["):])
    assert data[0]["komga"]["id"] == "b1" and data[0]["matched_by"] == "name and size"


def test_show_missing_lists_the_komga_books_without_a_library_file(cmd_env):
    session = FakeSession([book("b1", cmd_env["url"], cmd_env["size"]), book("b2", "/k/Other.cbz", 3)])
    out = io.StringIO()
    assert komga_cmd.run(["show", "--missing"], out=out, session=session) == 0
    assert out.getvalue().strip().splitlines() == ["/k/Other.cbz"]


def test_books_use_the_list_call_and_fall_back_to_the_deprecated_one():
    config = Komga(url="http://k", api_key="K", username=None, password=None, lock=True, timeout=5)
    session = FakeSession([book("b1", "/k/a.cbz", 1)])
    assert [b["id"] for b in komga.Client(config, session).books()] == ["b1"]
    assert session.calls[0][:2] == ("POST", "api/v1/books/list")
    session = FakeSession([book("b1", "/k/a.cbz", 1)])
    session.no_list = True
    assert [b["id"] for b in komga.Client(config, session).books()] == ["b1"]
    assert [c[:2] for c in session.calls] == [("POST", "api/v1/books/list"), ("GET", "api/v1/books")]


def test_fields_pushes_only_the_chosen_ones(cmd_env):
    _, _, session, _ = send(["push", "-y", "--fields", "title,number"], cmd_env)
    patches = {p: body for m, p, body in session.calls if m == "PATCH"}
    assert list(patches) == ["api/v1/books/b1/metadata"]  # no series field chosen: the series is left alone
    assert "title" in patches["api/v1/books/b1/metadata"] and "isbn" not in patches["api/v1/books/b1/metadata"]
    _, _, session, _ = send(["push", "-y", "--fields", "publisher,genres"], cmd_env)
    patches = {p: body for m, p, body in session.calls if m == "PATCH"}
    assert list(patches) == ["api/v1/series/s1/metadata"] and "title" not in patches["api/v1/series/s1/metadata"]


def test_an_unknown_field_is_refused(cmd_env, capsys):
    import pytest
    with pytest.raises(SystemExit):
        send(["push", "--fields", "colour"], cmd_env)
    assert "unknown field" in capsys.readouterr().err


def test_options_may_come_before_or_after_the_query(cmd_env):
    for argv in (["push", "-n", "series:nains"], ["push", "series:nains", "-n"], ["show", "series:nains", "-v"]):
        code, _, session, _ = send(argv, cmd_env)
        assert code == 0


def test_a_link_is_shown_with_its_full_address():
    assert komga_cmd._value([{"label": "Bédéthèque", "url": "https://www.bedetheque.com/BD-Nains-1-" + "x" * 80 + ".html"}]
                            ).startswith("Bédéthèque: https://www.bedetheque.com/BD-Nains-1-xxx") 
    assert komga_cmd._value([{"label": "Bédéthèque", "url": "https://u"}]) == "Bédéthèque: https://u"


SERIES_RECORD = {"summary": "Des nains.", "url": "https://m.bedetheque.com/serie-1-BD-Nains.html",
                 "extra": {"status": "En cours", "albums": [{"volume": 1}, {"volume": 2}, {"volume": None}]}}


def test_translator_and_other_credits_are_sent_with_their_roles():
    album = {**ALBUM, "credits": {"Writer": ["A"], "Translator": ["T"], "Other": ["O"], "Editor": ["E"]}}
    assert komga.book_fields(album)["authors"] == [
        {"name": "A", "role": "writer"}, {"name": "E", "role": "editor"}, {"name": "T", "role": "translator"},
        {"name": "O", "role": "other"}]


def test_genres_become_tags_added_to_the_ones_komga_has():
    fields = komga.book_fields(ALBUM, {"tags": ["mine", "fantasy"]})
    assert fields["tags"] == ["fantasy", "mine"]  # one spelling, Komga's
    # nothing differs when Komga already has the genre (any case) and the user's own tag
    assert komga.changes({"tags": ["Fantasy", "mine"]}, {"tags": ["fantasy", "mine"], "tagsLock": True},
                         komga.BOOK_LOCKS, True) == ({}, [])


def test_the_series_gets_summary_link_status_and_the_count_of_numbered_albums():
    fields = komga.series_fields([{**ALBUM, "series_record": SERIES_RECORD}])
    assert fields["summary"] == "Des nains." and fields["status"] == "ONGOING"
    assert fields["totalBookCount"] == 2  # the album without a number (an integral) is not counted
    assert fields["links"] == [{"label": "Bédéthèque", "url": SERIES_RECORD["url"]}]


def test_the_status_words_of_bedetheque():
    assert [komga.status_of(w) for w in ("En cours", "Terminée", "Série abandonnée", "Suspendue", "Une autre", None)] == [
        "ONGOING", "ENDED", "ABANDONED", "HIATUS", None, None]


def test_a_series_without_a_record_sends_only_what_it_had(cmd_env):
    fields = komga.series_fields([ALBUM])
    assert set(fields) == {"title", "publisher", "language", "genres"}


def test_fields_can_name_the_new_ones(cmd_env):
    _, _, session, _ = send(["push", "-y", "--fields", "tags,series-total"], cmd_env)
    assert [p for m, p, _ in session.calls if m == "PATCH"] == ["api/v1/books/b1/metadata"]  # no total known here
    patches = {p: b for m, p, b in session.calls if m == "PATCH"}
    assert set(patches["api/v1/books/b1/metadata"]) <= {"tags", "tagsLock"}


def test_the_credits_bedetheque_has_and_comicinfo_has_not_are_sent_with_their_own_role():
    authors = komga.authors_of({"credits": {"Inker": ["A"], "Préface": ["B"], "Storyboard": ["C"], "Adapté de": ["D"]}})
    assert {(a["name"], a["role"]) for a in authors} == {("A", "inker"), ("B", "foreword"), ("C", "storyboard"), ("D", "adapted from")}
