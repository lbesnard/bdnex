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


def test_a_changed_size_still_matches_a_name_that_is_unique_in_a_folder_of_the_same_name():
    out = komga.match([file("/c/Nains/a.pdf", 10)], [book("b1", "/k/Nains/a.pdf", 99)])
    assert out["pairs"][0][2] == "name"


def test_a_generic_name_in_another_folder_is_not_taken_for_the_file_when_the_size_differs():
    """`01.cbz` of one series is not `01.cbz` of another: pairing them would push one series' title, authors and locks onto the other's book."""
    out = komga.match([file("/c/Nains/01.cbz", 10)], [book("b1", "/k/Elfes/01.cbz", 99)])
    assert not out["pairs"] and [f["path"] for f in out["no_book"]] == ["/c/Nains/01.cbz"]
    same_size = komga.match([file("/c/Nains/01.cbz", 10)], [book("b1", "/k/Elfes/01.cbz", 10)])
    assert same_size["pairs"][0][2] == "name and size"  # name AND size is still enough


def test_names_are_compared_composed_and_without_case():
    composed, decomposed = "Astérix.cbz", "Aste\u0301rix.CBZ"  # the same name, written two ways on two systems
    out = komga.match([file("/c/Gaulois/" + composed, 10)], [book("b1", "/k/Gaulois/" + decomposed, 10)])
    assert out["pairs"] and out["pairs"][0][2] == "name and size"


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


def test_an_album_not_read_again_since_a_role_was_renamed_still_sends_its_inker():
    """`Encrage` was stored before the parser called it `Inker`: until `bdnex reparse`, it must not be dropped."""
    from bdnex.library import AlbumInfo, Library

    lib = Library(":memory:")
    album_id = lib.add_album(AlbumInfo("bedetheque", "1", "T", credits={"Writer": ["A"]}))
    person = lib.conn.execute("INSERT INTO people(name) VALUES ('Encreur')").lastrowid
    lib.conn.execute("INSERT INTO album_credits(album_id, person_id, role) VALUES (?, ?, 'Encrage')", (album_id, person))
    album = lib.get_album(album_id)
    assert album["credits"]["Inker"] == ["Encreur"] and "Encrage" not in album["credits"]
    assert {"name": "Encreur", "role": "inker"} in komga.authors_of(album)


def test_push_leaves_out_the_files_whose_match_waits_for_a_review(cmd_env, tmp_path, capsys):
    db = tmp_path / "lib.sqlite"
    with Library(db) as lib:
        lib.flag_review(lib.query([], files=True)[0]["path"], "another year")
    code, _, session, _ = send(["push", "-y"], cmd_env)
    assert code == 0 and not [c for c in session.calls if c[0] == "PATCH"]
    assert "left out" in capsys.readouterr().err
    _, _, session, _ = send(["push", "-y", "--with-review"], cmd_env)
    assert [p for m, p, _ in session.calls if m == "PATCH"] == ["api/v1/books/b1/metadata", "api/v1/series/s1/metadata"]
    _, text, _, _ = send(["show"], cmd_env)
    assert "Nains T01.cbz" in text  # `show` is read-only: it still lists it


# ---- from the review of the Komga integration
CONFIG = Komga(url="http://k", api_key="K", username=None, password=None, lock=True, timeout=5)


class Redirecting(FakeSession):
    """A Komga behind a proxy that answers every call with a redirect to another host."""

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("allow_redirects")))
        response = FakeResponse(None, 301)
        response.headers = {"Location": "https://elsewhere.example/api/v1/books/list"}
        return response


def test_a_redirect_is_not_followed_so_the_key_never_goes_to_another_host():
    import pytest

    session = Redirecting([])
    with pytest.raises(komga.KomgaError, match="redirected.*elsewhere.example"):
        list(komga.Client(CONFIG, session).books())
    assert all(call[2] is False for call in session.calls)  # `allow_redirects=False` on every request


def test_the_address_must_be_a_web_address():
    import pytest

    with pytest.raises(komga.KomgaError, match="http://"):
        komga.Client(Komga(url="ftp://k", api_key="K", username=None, password=None, lock=True, timeout=5), FakeSession([]))


def test_the_komga_address_cannot_be_changed_from_the_settings_page():
    from bdnex.api import settings_api

    assert "komga.url" in settings_api.FIXED  # a new address would receive the stored key on the next push


class Paged(FakeSession):
    """Komga pages of two books each, optionally without the `last` flag, with a book shifted onto the next page mid-listing."""

    def __init__(self, pages, last_flag=True):
        super().__init__([])
        self.pages, self.last_flag = pages, last_flag

    def request(self, method, url, **kwargs):
        page = kwargs["params"]["page"]
        data = {"content": self.pages[page] if page < len(self.pages) else [], "totalPages": len(self.pages)}
        if self.last_flag:
            data["last"] = page >= len(self.pages) - 1
        return FakeResponse(data)


def test_pages_are_read_to_the_end_without_a_last_flag_and_a_shifted_book_is_not_listed_twice():
    pages = [[book("b1", "/k/1.cbz", 1), book("b2", "/k/2.cbz", 1)], [book("b2", "/k/2.cbz", 1), book("b3", "/k/3.cbz", 1)]]
    assert [b["id"] for b in komga.Client(CONFIG, Paged(pages, last_flag=False)).books()] == ["b1", "b2", "b3"]  # a missing `last` is not "stop"
    assert [b["id"] for b in komga.Client(CONFIG, Paged(pages)).books()] == ["b1", "b2", "b3"]  # b2 was on both pages


def test_the_deprecated_call_is_only_the_fallback_for_a_server_that_does_not_take_the_list_call():
    import pytest

    class Failing(FakeSession):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url))
            return FakeResponse("boom", 500)  # a passing server error: not "this call does not exist"

    session = Failing([])
    with pytest.raises(komga.KomgaError, match="500"):
        list(komga.Client(CONFIG, session).books())
    assert len(session.calls) == 1  # no second, different call on a 500


def test_status_words_with_a_negation_are_not_read_as_the_word_they_contain():
    assert komga.status_of("Terminée") == "ENDED" and komga.status_of("En cours") == "ONGOING"
    assert komga.status_of("Non terminée") == "ONGOING"  # not "ended"
    assert komga.status_of("Série non terminée") == "ONGOING"
    assert komga.status_of("Discontinuée") == "ABANDONED"  # not "ongoing"
    assert komga.status_of("Interrompue") == "ABANDONED" and komga.status_of("") is None


def test_a_volume_stored_as_a_float_is_the_number_komga_holds():
    base = {"title": "T", "credits": {}, "genres": []}
    assert komga.book_fields({**base, "volume": 1.0})["number"] == "1" and komga.book_fields({**base, "volume": 1})["number"] == "1"
    fields = komga.book_fields({**base, "volume": 1.5})
    assert fields["number"] == "1.5" and fields["numberSort"] == 1.5
    same, _ = komga.changes({"number": "1"}, {"number": "1", "numberLock": True}, komga.BOOK_LOCKS, True)
    assert same == {}  # and it is the same as Komga's `1`: nothing is pushed on every run


def test_the_authors_the_library_has_no_names_for_stay_in_komga_and_series_genres_are_added_not_replaced():
    album = {"title": "T", "credits": {"Writer": ["Jarry"]}, "genres": ["Fantasy"]}
    current = {"authors": [{"name": "Old Writer", "role": "writer"}, {"name": "Someone", "role": "colorist"}], "tags": []}
    authors = komga.book_fields(album, current)["authors"]
    assert {"name": "Jarry", "role": "writer"} in authors and {"name": "Someone", "role": "colorist"} in authors  # a role added in Komga stays
    assert {"name": "Old Writer", "role": "writer"} not in authors  # the library's writers replace Komga's writers
    fields = komga.series_fields([{"series": "Nains", "title": "T", "genres": ["Fantasy"]}], {"genres": ["Humour", "fantasy"]})
    assert fields["genres"] == ["fantasy", "Humour"]  # Komga's spelling wins, and its own genre is not erased


def test_a_komga_folder_that_holds_several_series_gets_no_series_data():
    session = FakeSession([], series_meta={"title": "Folder"})
    client = komga.Client(CONFIG, session)
    args = type("A", (), {"only": None, "wanted": (None, None)})()
    one, two = {**ALBUM, "series": "Nains"}, {**ALBUM, "series": "Elfes", "title": "Other"}
    plan = komga_cmd._plan(client, [(file("/c/a.pdf", 1, 1), book("b1", "/k/a.pdf", 1), "name and size"),
                                    (file("/c/b.pdf", 1, 2), book("b2", "/k/b.pdf", 1), "name and size")], {1: one, 2: two}, args, True)
    series = [step for step in plan if step["kind"] == "series"]
    assert len(series) == 1 and series[0]["payload"] == {} and "2 different series" in series[0]["skipped"]
    out = io.StringIO()
    komga_cmd._show(plan, {"no_book": [], "ambiguous": []}, type("A", (), {"verbose": 0, "pretend": True})(), out, False)
    assert "2 different series" in out.getvalue()  # and it is said, not counted as "already up to date"


# ------------------------------------------------------------------- a field locked in Komga, and a push that stops
def test_a_field_locked_in_komga_that_differs_is_left_alone_unless_told_otherwise():
    current = {"title": "Chosen in Komga", "titleLock": True, "summary": "old", "summaryLock": False}
    fields = {"title": "Redwin", "summary": "Un nain."}
    locked: list[str] = []
    payload, differing = komga.changes(fields, current, komga.BOOK_LOCKS, True, True, locked)
    assert "title" not in payload and locked == ["title"]  # someone locked it there: not overwritten
    assert payload["summary"] == "Un nain." and payload["summaryLock"] is True and differing == ["summary"]
    payload, differing = komga.changes(fields, current, komga.BOOK_LOCKS, True, False)  # --overwrite-locked
    assert payload["title"] == "Redwin" and "title" in differing
    same = {"title": "Redwin", "titleLock": True}
    assert komga.changes({"title": "Redwin"}, same, komga.BOOK_LOCKS, True, True, locked)[0] == {}  # the same value: nothing, locked or not


def test_the_plan_counts_the_locked_fields_it_left_alone_and_the_flag_changes_them():
    meta = {"title": "Chosen in Komga", "titleLock": True}
    session = FakeSession([], series_meta={"title": "Nains"})
    client = komga.Client(Komga(url="http://k", api_key="K", username=None, password=None, lock=True, timeout=5), session)
    pair = [(file("/c/a.pdf", 1), book("b1", "/k/a.pdf", 1, metadata=meta), "name and size")]
    args = type("A", (), {"only": "books", "wanted": (None, None), "overwrite_locked": False})()
    plan = komga_cmd._plan(client, pair, {1: ALBUM}, args, True)
    assert plan[0]["locked"] == ["title"] and "title" not in plan[0]["payload"]
    args.overwrite_locked = True
    plan = komga_cmd._plan(client, pair, {1: ALBUM}, args, True)
    assert plan[0]["locked"] == [] and plan[0]["payload"]["title"] == "Redwin"


def test_a_push_stops_after_five_failures_in_a_row_and_says_how_to_finish_it():
    class Down:
        def patch_book(self, id_, payload):
            raise komga.KomgaError("the server is down")

        patch_series = patch_book

    todo = [{"kind": "book", "id": f"b{n}", "label": f"book {n}", "payload": {"title": "x"}} for n in range(40)]
    out = io.StringIO()
    assert komga_cmd._send(Down(), todo, out, False) == 1
    text = out.getvalue()
    assert text.count("the server is down") == komga_cmd.GIVE_UP_AFTER and "35 more not tried" in text  # not forty requests that all fail
    assert "again" in text  # it only sends what differs, so a second run finishes it


def test_an_isbn_is_sent_only_when_it_is_a_valid_one_in_thirteen_digits():
    assert komga.valid_isbn("978-2-205-07352-2") == "9782205073522"
    assert komga.valid_isbn("2-205-07352-X") is None and komga.valid_isbn("2205073524") == "9782205073522"  # ISBN-10 becomes ISBN-13
    assert komga.valid_isbn("9782205073523") is None and komga.valid_isbn("123") is None and komga.valid_isbn(None) is None
    album = {**ALBUM, "isbn": "9782205073523"} if "ALBUM" in globals() else None
    if album:
        assert "isbn" not in komga.book_fields(album, {})


class Refusing:
    """A Komga that answers 400 to the first update of a book, naming the field (what an invalid ISBN does)."""

    def __init__(self, body):
        self.headers, self.auth, self.calls, self.body = {}, None, [], body

    def request(self, method, url, **kwargs):
        self.calls.append(kwargs.get("json"))
        if len(self.calls) == 1:
            response = FakeResponse(self.body, 400)
            response.text = self.body
            return response
        return FakeResponse(None)


def test_a_field_komga_refuses_is_left_out_and_the_rest_is_sent_again():
    body = '{"violations":[{"fieldName":"isbn","message":"invalid ISBN"},{"fieldName":"isbn","message":"must be null"}]}'
    session = Refusing(body)
    left_out = komga.Client(CONFIG, session).patch_book("b1", {"title": "T", "titleLock": True, "isbn": "9", "isbnLock": True})
    assert left_out == ["isbn"] and session.calls[1] == {"title": "T", "titleLock": True}


def test_a_refusal_that_names_no_field_of_ours_is_still_an_error():
    import pytest

    with pytest.raises(komga.KomgaError, match="400"):
        komga.Client(CONFIG, Refusing('{"error": "nope"}')).patch_book("b1", {"title": "T"})


class PickyKomga:
    """Refuses (400, naming no field) any update that holds an ``isbn``; accepts the others."""

    def __init__(self):
        self.headers, self.auth, self.calls = {}, None, []

    def request(self, method, url, **kwargs):
        body = kwargs.get("json")
        self.calls.append(body)
        if "isbn" in body:
            response = FakeResponse("bad", 400)
            response.text = '{"message": "Validation failed"}'
            return response
        return FakeResponse(None)


def test_when_komga_names_no_field_each_field_is_tried_apart_so_the_valid_ones_still_go(caplog):
    session = PickyKomga()
    payload = {"title": "T", "titleLock": True, "isbn": "9", "isbnLock": True, "summary": "S", "summaryLock": True}
    with caplog.at_level("WARNING"):
        left_out = komga.Client(CONFIG, session).patch_book("b1", payload)
    assert left_out == ["isbn"]
    accepted = [c for c in session.calls[1:] if "isbn" not in c]
    assert {"title": "T", "titleLock": True} in accepted and {"summary": "S", "summaryLock": True} in accepted
    assert "refused isbn" in caplog.text  # the problem is in the log, not only in the window


def test_a_series_update_leaves_out_a_refused_field_too():
    body = '{"violations":[{"fieldName":"language","message":"bad"}]}'
    session = Refusing(body)
    assert komga.Client(CONFIG, session).patch_series("s1", {"title": "T", "titleLock": True, "language": "xx", "languageLock": True}) == ["language"]
    assert session.calls[1] == {"title": "T", "titleLock": True}
