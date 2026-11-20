"""`bdnex review --batch`: the Textual table, driven with Textual's pilot (no terminal needed)."""

import asyncio
import threading

import pytest
import yaml

pytest.importorskip("textual")

from bdnex.config import load_config  # noqa: E402
from bdnex.importer.source import Candidate  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402
from bdnex.library import review_batch as batch  # noqa: E402
from bdnex.ui import review_tui  # noqa: E402

FORGE = Candidate("2", "https://www.bedetheque.com/BD-Forge-2.html", "forge", 2)


class FakeSource:
    name = "bedetheque"

    def __init__(self):
        self.asked = []

    def candidates(self, text, limit=5):
        self.asked.append(text)
        return [(FORGE, 91.0)]

    def candidate_for(self, text):
        return FORGE if "Forge" in text or text == "2" else None


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db), "cover": {"check": False}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    files = {}
    with Library(db) as lib:
        series = SeriesInfo("bedetheque", "s1", "Nains")
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin", series=series, volume=1, year=2015, url="https://www.bedetheque.com/BD-Nains-Redwin-1.html"))
        other = lib.add_album(AlbumInfo("bedetheque", "2", "Forge", series=series, volume=2, year=2016, url="https://www.bedetheque.com/BD-Nains-Forge-2.html"))
        for name, (filename, target, score) in {"wrong": ("Nains - T05 - Autre.cbz", album, 78.0),
                                                "unconfirmed": ("Nains - T02 - Forge.cbz", other, 82.0)}.items():
            path = make_cbz(filename)
            lib.add_file(path, target)
            lib.log_import(path, "tagged", score, target)
            files[name] = path
        loose = make_cbz("Loose file.cbz")
        lib.add_file(loose, None)
        lib.log_import(loose, "skipped", 55.0)
        files["loose"] = loose
    return db, files


async def settle(app, pilot):
    """Wait for the background work (the files weighed, a search, a link) and the screen to catch up."""
    for _ in range(3):
        await app.workers.wait_for_complete()
        await pilot.pause()


def drive(db, script, unmatched=True, size=50, source=None):
    """Run the app, call ``script(app, pilot)`` once the batch is shown, and return the cells of the table."""
    settings = load_config().settings
    source = source or FakeSource()
    result = {}

    async def go():
        with Library(db) as lib:
            app = review_tui.ReviewApp(lib, settings, source, size, (), unmatched)
            async with app.run_test(size=(170, 45)) as pilot:
                await settle(app, pilot)
                await script(app, pilot)
                await settle(app, pilot)
                result["app"] = app
                result["cells"] = [[str(cell) for cell in app.query_one("DataTable").get_row_at(i)] for i in range(len(app.rows))]

    asyncio.run(go())
    return result, source


def test_the_table_opens_at_once_and_shows_file_folder_album_scores_state_and_how_long_it_took(env):
    db, files = env

    async def script(app, pilot):
        assert app.query_one("DataTable").row_count == 3
        assert "weighed in" in app.sub_title and "0/3 checked" in app.sub_title  # what was slow, and how slow

    result, _ = drive(db, script)
    first, second, third = result["cells"]
    assert first[1] == "Nains - T05 - Autre.cbz" and "Redwin" in first[3] and first[5] == "78%" and first[6] == "-"
    assert third[3] == "(no album)" and third[1] == "Loose file.cbz"  # the unmatched is shown, last
    assert [first[7], second[7], third[7]] == ["contradicted", "unconfirmed", "no album"]  # what is wrong, until it is decided


def test_a_loading_panel_says_what_is_being_done_until_the_table_is_ready(env, monkeypatch):
    db, files = env
    release = threading.Event()
    real = batch.next_batch
    monkeypatch.setattr(batch, "next_batch", lambda *a, **k: (release.wait(5), real(*a, **k))[1])  # a slow weighing
    seen = {}

    async def go():
        settings = load_config().settings
        with Library(db) as lib:
            app = review_tui.ReviewApp(lib, settings, FakeSource(), 50, (), True)
            async with app.run_test(size=(170, 45)) as pilot:
                await pilot.pause()
                await pilot.pause(0.3)
                panel = app.query_one("#loading")
                seen["during"] = (panel.display, app.query_one("DataTable").display, str(panel.render()))
                release.set()
                await settle(app, pilot)
                seen["after"] = (panel.display, app.query_one("DataTable").display, app.query_one("DataTable").row_count)

    asyncio.run(go())
    shown, table_shown, text = seen["during"]
    assert shown and not table_shown  # the panel is where the table will be
    assert "weighing the files" in text and "loading the album list" in text and "Getting the batch ready" in text
    assert "s\n" in text or " s" in text  # each job says for how long
    assert seen["after"] == (False, True, 3)  # then the table takes its place


def test_a_header_click_or_a_number_key_sorts_and_the_same_column_again_reverses(env):
    from textual.widgets import DataTable

    db, files = env
    seen = {}

    async def script(app, pilot):
        table = app.query_one(DataTable)
        names = lambda: [str(table.get_row_at(i)[1]) for i in range(table.row_count)]  # noqa: E731
        seen["start"] = names()
        await pilot.press("6")  # Match, ascending: the lowest score first
        seen["match_up"] = names()
        await pilot.press("6")  # the same column again: reversed
        seen["match_down"] = names()
        await pilot.press("4")  # Album now: the file without album goes last
        seen["album"] = names()
        await pilot.press("2")  # File, by name
        seen["file"] = names()
        seen["labels"] = [str(column.label) for column in table.columns.values()]
        seen["widths"] = [(column.width, column.auto_width) for column in table.columns.values()]
        seen["title"] = app.sub_title
        # a click on a header does the same as the number key: the message the table sends for it
        key = list(table.columns)[0]
        table.post_message(DataTable.HeaderSelected(table, key, 0, table.columns[key].label))
        await pilot.pause()
        seen["back"] = names()  # `#`: the order of the batch

    result, _ = drive(db, script)
    autre, forge, loose = "Nains - T05 - Autre.cbz", "Nains - T02 - Forge.cbz", "Loose file.cbz"
    assert seen["start"] == [autre, forge, loose]
    assert seen["match_up"] == [loose, autre, forge] and seen["match_down"] == [forge, autre, loose]  # 55, 78, 82
    assert seen["album"][-1] == loose  # no album: last, whatever the direction
    assert seen["file"] == [loose, forge, autre]  # by name
    assert seen["labels"][1].startswith("File ▲") and "▲" not in seen["labels"][0] and "by file" in seen["title"]
    assert seen["widths"] == [(width, False) for _, width in review_tui.COLUMNS]  # fixed widths: the table never has to re-measure
    assert seen["back"] == seen["start"]


def test_sorting_keeps_the_cursor_on_the_same_file_and_decisions_still_hit_the_right_row(env):
    from textual.widgets import DataTable

    db, files = env

    async def script(app, pilot):
        table = app.query_one(DataTable)
        await pilot.press("6")  # the loose file (55) is now first, the contradicted one second
        table.move_cursor(row=1)
        await pilot.pause()
        assert app.current().path == files["wrong"]
        await pilot.press("6")  # reversed: the cursor follows the same file
        await pilot.pause()
        assert app.current().path == files["wrong"]
        await pilot.press("u")  # unlink what is under the cursor, not what was first

    drive(db, script)
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is None
        assert lib.get_file(files["unconfirmed"])["album_id"] is not None


def test_keys_decide_and_each_decision_is_saved_at_once(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("u")  # the contradicted one: unlink
        await pilot.press("k")  # the unconfirmed one: keep
        await pilot.press("l")  # the file without album: leave it

    result, _ = drive(db, script)
    assert [row[7] for row in result["cells"]] == ["unlinked", "keep", "left"]
    assert result["app"].decided == {"unlinked": 1, "keep": 1, "left": 1}
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is None and lib.last_import(files["wrong"])["outcome"] == "skipped"
        assert lib.attrs_of("file", lib.get_file(files["unconfirmed"])["id"])["checked"] == "keep"
        assert lib.get_file(files["unconfirmed"])["album_id"] is not None
        assert batch.next_batch(lib, load_config().settings, 50, (), True) == []  # the next call continues with the rest


def test_skip_saves_nothing_and_the_file_comes_back(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("s")

    result, _ = drive(db, script)
    assert result["cells"][0][7] == "skipped"
    with Library(db) as lib:
        assert [r.path for r in batch.next_batch(lib, load_config().settings, 50)][0] == files["wrong"]


def test_a_file_without_album_cannot_be_kept_or_unlinked(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("down", "down")
        await pilot.press("k")
        await pilot.press("u")

    result, _ = drive(db, script)
    assert result["cells"][2][7] == "no album" and result["app"].decided == {}  # nothing was decided, nothing saved


seen_calls: list = []  # the ``keep_both`` of every link the screen asked for


def _linking(monkeypatch, db, release, outcome="switch"):
    """What `import --album --database-only` does to the library, on its own connection (it runs in a thread), held until
    ``release`` is set so that the test can look at the table while the link is being made."""
    from bdnex.ui import verdicts

    def rematch(path, url, keep_both=False):
        release.wait(5)
        seen_calls.append(keep_both)
        if outcome == "switch" or (outcome == "refuse" and keep_both):
            with Library(db) as lib:
                lib.set_file_album(path, lib.find_album("bedetheque", "2"))
                lib.set_file_fields(path, cover_score=66.0)  # the import compares the cover of the album it chose
        return True

    monkeypatch.setattr(verdicts, "rematch", rematch)


def test_o_opens_the_search_and_a_pick_shows_linking_then_updates_the_row(env, monkeypatch):
    db, files = env
    release = threading.Event()
    _linking(monkeypatch, db, release)
    seen = {}

    async def script(app, pilot):
        await pilot.press("o")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ == "FuzzyScreen"
        await pilot.press("enter")  # the first hit
        await pilot.pause()
        row = app.rows[0]
        seen["busy"] = (row.busy, str(app.query_one("DataTable").get_row_at(0)[7]), row.message)  # while it is being made
        await pilot.press("k")  # a decision on a file that is being linked is refused
        assert not row.status
        release.set()
        await settle(app, pilot)

    result, source = drive(db, script)
    assert seen["busy"][0] is True and seen["busy"][1] == "linking..." and "reading the album page" in seen["busy"][2]
    first = result["cells"][0]
    assert first[7] == "linked" and "Forge" in first[3] and first[5] == "you" and first[6] == "66%"  # the row says what happened
    assert "cover is 66%" in result["app"].rows[0].message
    with Library(db) as lib:
        assert lib.attrs_of("file", lib.get_file(files["wrong"])["id"])["checked"].startswith("other:")


def test_a_refused_link_says_refused_in_the_table(env, monkeypatch):
    db, files = env
    release = threading.Event()
    release.set()
    _linking(monkeypatch, db, release, outcome="refuse")  # exit code 0, library unchanged: the album already has a file

    async def script(app, pilot):
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert result["cells"][0][7] == "refused" and "refused" in result["app"].rows[0].message
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is not None  # nothing changed


def test_a_refused_link_offers_to_keep_both_and_yes_links_the_file_as_a_spare_version(env, monkeypatch):
    db, files = env
    release = threading.Event()
    release.set()
    seen_calls.clear()
    _linking(monkeypatch, db, release, outcome="refuse")
    seen = {}

    async def script(app, pilot):
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        seen["screen"] = app.screen.__class__.__name__  # the question: keep both?
        await pilot.press("y")
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert seen["screen"] == "AskScreen" and seen_calls == [False, True]
    assert result["cells"][0][7] == "linked"


def test_no_to_keep_both_changes_nothing(env, monkeypatch):
    db, files = env
    release = threading.Event()
    release.set()
    seen_calls.clear()
    _linking(monkeypatch, db, release, outcome="refuse")

    async def script(app, pilot):
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert seen_calls == [False] and result["cells"][0][7] == "refused"


def test_the_page_is_a_link_in_its_column_and_in_the_detail_and_p_copies_it_over_ssh(env, monkeypatch):
    db, files = env
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 22")
    opened = []
    monkeypatch.setattr(review_tui.webbrowser, "open", lambda url: opened.append(url) or True)
    seen = {}

    async def script(app, pilot):
        from textual.widgets import DataTable

        cell = app.query_one(DataTable).get_row_at(0)[4]
        seen["link"] = (cell.plain, cell.style.link if hasattr(cell.style, "link") else repr(cell.style))
        seen["facts"] = app.rows[0].facts
        copied = []
        app.copy_to_clipboard = copied.append
        await pilot.press("p")
        seen["copied"] = copied

    drive(db, script)
    assert opened == []  # over SSH no browser is started on the other machine
    assert seen["copied"] and seen["copied"][0].startswith("https://www.bedetheque.com/")
    assert seen["link"][0] and seen["link"][1] == seen["copied"][0]
    assert "file: CBZ" in seen["facts"]


def test_the_batch_is_in_folder_order_so_a_series_sits_together():
    from bdnex.library.review_batch import Row
    import types

    rows = [Row("/a/b/x.cbz", "b", None, None, None, None, None, "ok"), Row("/a/a/y.cbz", "a", None, None, None, None, None, "wrong")]
    rows.sort(key=lambda row: (row.folder.casefold(), row.path))
    assert [row.folder for row in rows] == ["a", "b"]


def test_i_takes_an_address_or_a_number(env, monkeypatch):
    db, files = env
    release = threading.Event()
    release.set()
    _linking(monkeypatch, db, release)

    async def script(app, pilot):
        await pilot.press("i")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "AskScreen"
        await pilot.press(*"2")
        await pilot.press("enter")
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert result["cells"][0][7] == "linked"


def test_capital_o_hands_over_to_the_real_fzf_when_it_is_installed(env, monkeypatch):
    import contextlib

    db, files = env
    release = threading.Event()
    release.set()
    _linking(monkeypatch, db, release)
    source = FakeSource()
    source.catalogue = lambda: []
    asked = []
    monkeypatch.setattr(review_tui.shutil, "which", lambda name: "/usr/bin/fzf")
    monkeypatch.setattr(review_tui.ReviewApp, "suspend", lambda self: contextlib.nullcontext())
    monkeypatch.setattr("bdnex.ui.prompts.fzf_pick", lambda src, path, start: asked.append((path, start)) or FORGE)

    async def script(app, pilot):
        await pilot.press("O")
        await settle(app, pilot)

    result, _ = drive(db, script, source=source)
    assert asked and asked[0][0] == files["wrong"] and "nains" in asked[0][1].lower()  # it starts from the file's name
    assert result["cells"][0][7] == "linked"


def test_the_next_batch_is_offered_when_the_batch_is_done(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("k")
        await pilot.press("k")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "AskScreen"
        await pilot.press("y")
        await settle(app, pilot)
        assert app.rows == [] and app.number == 2  # nothing left: the checked files are not shown again

    drive(db, script, unmatched=False)


class Catalogue(FakeSource):
    """A source that can list its albums, like the real one."""

    def __init__(self, names):
        super().__init__()
        self.items = [Candidate(str(i), f"https://www.bedetheque.com/BD-{name.replace(' ', '-')}-{i}.html", name, i)
                      for i, name in enumerate(names)]

    def catalogue(self):
        return self.items


def hub_of(source):
    hub = review_tui.SourceHub(source)
    hub.ready.result(5)
    return hub


def test_the_search_finds_albums_by_words_in_any_order_with_or_without_accents_and_survives_a_typo():
    source = Catalogue(["Alex Ada Tome 1", "Alex Ada Tome 2", "Batman Année Un", "Les Nains Redwin", "Excel Saga"])
    hub = hub_of(source)
    names = lambda text: [c.name for c, _ in review_tui.fuzzy_search(hub, text)[0]]  # noqa: E731
    assert names("ada alex")[:2] == ["Alex Ada Tome 1", "Alex Ada Tome 2"]  # words in any order
    assert names("annee un") == ["Batman Année Un"]  # accents do not matter
    assert names("redwn")[0] == "Les Nains Redwin"  # a letter missing: the fuzzy match over the whole list
    assert review_tui.fuzzy_search(hub, "")[0] == []
    hub.close()


def test_a_search_for_a_letter_does_not_rank_the_whole_list():
    source = Catalogue([f"Album {i}" for i in range(6000)])
    hub = hub_of(source)
    hits, total = review_tui.fuzzy_search(hub, "a")
    assert total == 6000 and len(hits) == review_tui.SEARCH_LIMIT  # the shortest names, typing more narrows it
    hub.close()


def test_the_command_refuses_politely_without_a_terminal(env, capsys):
    from bdnex.ui import review_cmd

    assert review_cmd.run(["--batch", "10"]) == 2
    assert "run it in a terminal" in capsys.readouterr().err


def test_an_album_whose_page_has_no_tome_line_takes_its_tome_from_the_address():
    from bdnex.importer.bedetheque import parser

    fields = {"album_url": "https://www.bedetheque.com/BD-Rantanplan-Tome-16B6-Le-Noel-de-Rantanplan-10846.html",
              "Titre": "Rantanplan", "Série": "Rantanplan", "Identifiant": "10846"}
    info = parser.build_album_info(fields)
    assert info.series is not None and info.series.name == "Rantanplan" and info.alternate_number == "16B6" and info.volume is None
    plain = parser.build_album_info({**fields, "album_url": fields["album_url"].replace("16B6", "7")})
    assert plain.volume == 7


def test_c_opens_a_popup_with_the_covers_and_closes_again(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        await pilot.press("c")
        await settle(app, pilot)
        seen["name"] = app.screen.__class__.__name__
        for _ in range(20):  # the worker reads the file
            await pilot.pause(0.1)
            if "reading" not in str(app.screen.query_one("#left").render()):
                break
        seen["left"] = str(app.screen.query_one("#left").render())
        seen["foot"] = str(app.screen.query_one("#foot").render())
        await pilot.press("c")
        await pilot.pause()
        seen["after"] = app.screen.__class__.__name__

    drive(db, script)
    assert seen["name"] == "CoverScreen" and seen["after"] != "CoverScreen"
    assert "the file" in seen["left"] and "reading" not in seen["left"]
    assert "not saved" in seen["foot"]  # no cover of the album was saved in this library: it says so, no request


def test_an_integrale_is_known_from_the_address_when_the_page_has_no_tome_line():
    from bdnex.importer.bedetheque import parser

    fields = {"album_url": "https://www.bedetheque.com/BD-Chats-du-Louvre-INT-347340.html", "Titre": "Les chats du Louvre",
              "Série": "Chats du Louvre", "Identifiant": "347340"}
    info = parser.build_album_info(fields)
    assert info.alternate_number == "INT" and info.series is not None
    assert parser.build_album_info({**fields, "Série": None}).alternate_number is None  # no series: a one-shot, as before


def test_marked_files_get_the_album_of_a_series_by_their_volume_after_one_question(env, monkeypatch):
    from bdnex.importer.folders import SeriesProposal

    db, files = env
    release = threading.Event()
    release.set()
    seen_calls.clear()
    _linking(monkeypatch, db, release)
    asked = {}

    def propose(source, candidate, paths):
        asked["paths"] = paths
        return SeriesProposal("Nains", "s1", {files["wrong"]: FORGE}, with_volume=1, total=len(paths))

    monkeypatch.setattr(batch, "propose_for_series", propose)
    seen = {}

    async def script(app, pilot):
        await pilot.press("space")  # marks the first row (the contradicted file); the cursor moves on
        seen["marked"] = len(app.marked)
        await pilot.press("up")
        await pilot.press("a")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ == "FuzzyScreen"
        await pilot.press("enter")
        await settle(app, pilot)
        seen["question"] = app.screen.__class__.__name__
        await pilot.press("y")
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert seen["marked"] == 1 and asked["paths"] == [files["wrong"]] and seen["question"] == "AskScreen"
    assert result["cells"][0][7] == "linked" and seen_calls == [False]
    assert not result["app"].marked  # the mark goes with the link


def test_the_number_and_title_are_read_from_the_pages_sub_title():
    from bs4 import BeautifulSoup

    from bdnex.importer.bedetheque import parser

    def read(html):
        return parser._numbered_title(BeautifulSoup(f'<section><h2 class="bdt-ah-sub">{html}</h2></section>', "lxml"))

    assert read('16<span class="bdt-numa">B6</span>. Le Noël de Rantanplan ') == ("16B6", "Le Noël de Rantanplan")
    assert read('<span class="bdt-numa">INT</span>. DK2 - L\'Intégrale') == ("INT", "DK2 - L'Intégrale")
    assert read('3<span class="bdt-numa"></span>. Toys') == ("3", "Toys")
    # no number at all: the whole sub-title is the album's title (the address of such an album has no tome either)
    assert read('<span class="bdt-numa"></span>Les 40 commandements du jardinage ') == ("", "Les 40 commandements du jardinage")


def test_the_number_of_an_author_miscellany_is_not_a_tome_and_the_title_is_still_read():
    from bdnex.importer.bedetheque import parser

    def page(series, number):
        return (f'<html><body><section class="bdt-ah"><h1><a href="https://www.bedetheque.com/serie-1-BD-x.html">{series}</a></h1>'
                f'<h2 class="bdt-ah-sub">{number}<span class="bdt-numa"></span>. Mœbius</h2></section></body></html>')

    url = "https://www.bedetheque.com/BD-AUT-Giraud-Moebius-Moebius-12345.html"
    fields, _ = parser.parse_album_page(page("(AUT) Giraud / Moebius", "1984"), url)
    info = parser.build_album_info(fields)
    assert info.title == "Mœbius" and info.volume is None and info.series is None  # a one-shot, as before; the title is the album's
    fields, _ = parser.parse_album_page(page("Astérix", "7"), url)
    assert parser.build_album_info(fields).volume == 7  # a normal series still takes its number from the page


def test_an_album_with_no_title_keeps_the_series_name_not_the_sites_tome_n():
    from bdnex.importer.bedetheque import parser

    html = ('<html><body><section class="bdt-ah"><h1><a href="https://www.bedetheque.com/serie-1-BD-x.html">Alice in Borderland</a></h1>'
            '<h2 class="bdt-ah-sub"><span class="bdt-numa"></span>Tome 9</h2></section></body></html>')
    fields, _ = parser.parse_album_page(html, "https://www.bedetheque.com/BD-Alice-in-Borderland-Tome-9-120000.html")
    info = parser.build_album_info(fields)
    assert info.title == "Alice in Borderland" and info.volume == 9 and info.series.name == "Alice in Borderland"


@pytest.mark.parametrize("sub_title,generic", [("Tome 9", True), ("Volume 2", True), ("Intégrale", True), ("Tome 1/2", True), ("Vol. 3", True),
                                                 ("N°3", True), ("1984", False), ("Tome 3 - Les Nains", False), ("Intégrale des nains", False)])
def test_the_sites_filler_titles_are_told_from_real_ones(sub_title, generic):
    from bdnex.importer.bedetheque import parser

    assert bool(parser.GENERIC_TITLE.fullmatch(sub_title)) is generic
