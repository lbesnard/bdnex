"""`bdnex review --batch`: the Textual table, driven with Textual's pilot (no terminal needed)."""

import contextlib
import io
import os
import sys
import asyncio
import threading
import time

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


async def fetched(app, pilot, timeout=15.0):
    """Wait until the queue of the page and cover downloads is empty: it runs in its own thread, which `settle` (Textual's workers) does not
    wait for. Without this a slow machine looked at the library before the late import had ended (and the screen had answered it)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with app.remaining_lock:
            left = app.remaining
        if left <= 0:
            break
        await asyncio.sleep(0.05)
    await settle(app, pilot)


def drive(db, script, unmatched=True, size=50, source=None, columns=170, rows=45):
    """Run the app, call ``script(app, pilot)`` once the batch is shown, and return the cells of the table."""
    settings = load_config().settings
    source = source or FakeSource()
    result = {}

    async def go():
        with Library(db) as lib:
            app = review_tui.ReviewApp(lib, settings, source, size, (), unmatched)
            async with app.run_test(size=(columns, rows)) as pilot:
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
                await fetched(app, pilot)
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

    def rematch(path, url, keep_both=False, verbosity=0):
        release.wait(5)
        seen_calls.append(keep_both)
        if outcome == "switch" or (outcome == "refuse" and keep_both):
            with Library(db) as lib:
                lib.set_file_album(path, lib.find_album("bedetheque", "2"))
                lib.set_file_fields(path, cover_score=66.0)  # the import compares the cover of the album it chose
        return True

    monkeypatch.setattr(verdicts, "rematch", rematch)


def test_a_pick_is_written_at_once_and_the_page_and_cover_come_later(env, monkeypatch):
    db, files = env
    release = threading.Event()
    _linking(monkeypatch, db, release)  # the import of the page and cover is held: the site is slow
    seen = {}

    async def script(app, pilot):
        await pilot.press("o")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ == "FuzzyScreen"
        await pilot.press("enter")  # the first hit
        await pilot.pause()
        row = app.rows[0]
        with Library(db) as lib:  # written already, before anything was fetched
            seen["album"] = lib.get_album(lib.get_file(files["wrong"])["album_id"])["source_id"]
        seen["now"] = (row.status, row.pending, str(app.query_one("DataTable").get_row_at(0)[7]), row.message)
        release.set()
        await fetched(app, pilot)

    result, source = drive(db, script)
    assert seen["album"] == "2" and seen["now"][:2] == ("linked", True) and "linked · " in seen["now"][2] and "being fetched" in seen["now"][3]
    first = result["app"].rows[0]
    assert first.pending is False and "page and cover fetched" in first.message and "cover is 66%" in first.message


def test_closing_the_search_before_its_answer_arrives_does_not_crash(env, monkeypatch):
    db, files = env
    import time
    from bdnex.ui import review_tui

    real = review_tui.fuzzy_search
    monkeypatch.setattr(review_tui, "fuzzy_search", lambda hub, text: (time.sleep(0.5), real(hub, text))[1])

    async def script(app, pilot):
        await pilot.press("o")
        await pilot.press("escape")  # the search is still running in its worker
        await settle(app, pilot)
        await pilot.pause(0.5)
        assert app.screen.__class__.__name__ != "FuzzyScreen"

    result, source = drive(db, script)


def test_y_copies_the_log(env):
    db, files = env
    copied = []

    async def script(app, pilot):
        app.copy_to_clipboard = copied.append
        app.log_line("first")
        app.log_line("second")
        await pilot.press("y")
        await pilot.pause()

    drive(db, script)
    assert copied and "first" in copied[0] and copied[0].index("first") < copied[0].index("second")


def test_unlinking_while_the_page_is_fetched_wins_over_the_late_import(env, monkeypatch):
    db, files = env
    release = threading.Event()
    _linking(monkeypatch, db, release)  # the held import links the file back when it ends

    async def script(app, pilot):
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("up")  # the cursor moved on after the link
        await pilot.press("u")  # a mistake: unlink it now
        await pilot.pause()
        release.set()
        await fetched(app, pilot)

    drive(db, script)
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is None  # the late import did not put the link back


def test_a_link_to_an_album_that_has_another_file_is_made_at_once_as_a_spare_version(env, monkeypatch):
    """No refusal and no question: the file is linked to the metadata; which file stays is `bdnex duplicates`' job."""
    db, files = env
    release = threading.Event()
    release.set()
    seen_calls.clear()
    _linking(monkeypatch, db, release, outcome="refuse")  # the album has a file: only keep_both gets the link through
    seen = {}

    async def script(app, pilot):
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        seen["screen"] = app.screen.__class__.__name__

    result, _ = drive(db, script)
    assert seen["screen"] != "AskScreen" and seen_calls == [True]
    assert result["cells"][0][7] == "linked"


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


def test_slash_searches_the_table_and_brackets_cycle_through_matches(env):
    from textual.widgets import DataTable

    db, files = env
    seen = {}

    async def script(app, pilot):
        notified = []
        app.notify = lambda message, **kw: notified.append(message)
        table = app.query_one(DataTable)
        assert table.cursor_row == 0
        await pilot.press("/")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "AskScreen"
        await pilot.press(*"forge")
        await pilot.press("enter")
        await pilot.pause()
        seen["after_forge"] = table.cursor_row  # the only file whose name holds "forge"
        await pilot.press("/")
        await pilot.press(*"nains")
        await pilot.press("enter")
        await pilot.pause()
        seen["after_nains"] = table.cursor_row  # already on a match: Enter does not move it off
        await pilot.press("]")
        await pilot.pause()
        seen["next"] = table.cursor_row  # skips the row that does not match, wrapping around
        await pilot.press("]")
        await pilot.pause()
        seen["next_again"] = table.cursor_row  # only two matches: back to the first
        await pilot.press("[")
        await pilot.pause()
        seen["prev"] = table.cursor_row
        await pilot.press("/")
        await pilot.press(*"xyzzy")
        await pilot.press("enter")
        await pilot.pause()
        seen["no_match_cursor"] = table.cursor_row  # nothing holds it: the cursor stays
        seen["notified"] = list(notified)

    result, _ = drive(db, script)
    assert result["cells"][seen["after_forge"]][1] == "Nains - T02 - Forge.cbz"
    assert seen["after_nains"] == seen["after_forge"]
    assert seen["next"] != seen["after_nains"] and result["cells"][seen["next"]][1] == "Nains - T05 - Autre.cbz"
    assert seen["next_again"] == seen["after_nains"]
    assert seen["prev"] == seen["next"]
    assert seen["no_match_cursor"] == seen["prev"]
    assert any("No match for 'xyzzy'" in message for message in seen["notified"])


def test_brackets_say_so_when_there_is_no_search_yet(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        notified = []
        app.notify = lambda message, **kw: notified.append(message)
        await pilot.press("]")
        await pilot.pause()
        seen["notified"] = list(notified)

    result, _ = drive(db, script)
    assert any("No search yet" in message for message in seen["notified"])


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
        await pilot.press("k")  # a batch of one file, two to check: there is another batch
        await pilot.pause()
        assert len(app.waiting) == 1 and app.query_one("#queue-badge").display  # queued, with its red mark: not put over the table
        await pilot.press("w")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "AskScreen"
        await pilot.press("y")
        await settle(app, pilot)
        assert len(app.rows) == 1 and app.number == 2  # the checked file is not shown again

    drive(db, script, unmatched=False, size=1)


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
        await pilot.press("space")  # and the second: several files, so the volume of each is looked up
        seen["marked"] = len(app.marked)
        await pilot.press("a")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ == "FuzzyScreen"
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("w")  # the question waits in the queue (red mark, top right): `w` opens it
        await pilot.pause()
        seen["question"] = app.screen.__class__.__name__
        await pilot.press("y")
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert seen["marked"] == 2 and len(asked["paths"]) == 2 and seen["question"] == "AskScreen"
    assert result["cells"][0][7] == "linked" and seen_calls == [True]
    assert len(result["app"].marked) == 1  # the mark of the linked file went with the link; the other file found no volume and stays marked


def _series_address_run(env, monkeypatch, keys):
    from bdnex.importer.folders import SeriesProposal

    db, files = env
    release = threading.Event()
    release.set()
    seen_calls.clear()
    _linking(monkeypatch, db, release)
    asked = {}

    def propose(source, address, paths):
        asked["address"], asked["paths"] = address, paths
        return SeriesProposal("Nains", "s1", {files["wrong"]: FORGE}, with_volume=1, total=len(paths))

    monkeypatch.setattr(batch, "propose_for_series_address", lambda source, address, paths, found=None: propose(source, address, paths))
    monkeypatch.setattr(batch, "read_series_address", lambda settings, address: None)
    seen = {}

    async def script(app, pilot):
        await pilot.press("space")
        await pilot.press("up")
        await keys(app, pilot)
        await settle(app, pilot)
        await pilot.press("w")  # the question waits in the queue (red mark, top right): `w` opens it
        await pilot.pause()
        seen["question"] = app.screen.__class__.__name__
        await pilot.press("y")
        await settle(app, pilot)

    result, _ = drive(db, script)
    return asked, seen, result


SERIES_PAGE = "https://www.bedetheque.com/serie-18658-BD-Boys.html"


def test_a_series_address_in_the_search_attaches_the_marked_files_by_volume(env, monkeypatch):
    async def keys(app, pilot):
        await pilot.press("a")
        await settle(app, pilot)
        app.screen.query_one("#words").value = SERIES_PAGE
        await pilot.press("enter")

    asked, seen, result = _series_address_run(env, monkeypatch, keys)
    assert asked["address"] == SERIES_PAGE and len(asked["paths"]) == 1 and seen["question"] == "AskScreen"
    assert result["cells"][0][7] == "linked"


def test_i_with_a_series_address_attaches_the_files_too(env, monkeypatch):
    async def keys(app, pilot):
        await pilot.press("i")
        await settle(app, pilot)
        app.screen.query_one("Input").value = SERIES_PAGE
        await pilot.press("enter")

    asked, seen, result = _series_address_run(env, monkeypatch, keys)
    assert asked["address"] == SERIES_PAGE and seen["question"] == "AskScreen"


def test_a_series_page_gives_its_albums_by_volume():
    from bdnex.library import review_batch
    from bdnex.importer.bedetheque import Bedetheque
    from bdnex.importer.bedetheque import parser

    html = ('<html><body>'
            '<a href="https://www.bedetheque.com/BD-Boys-Tome-1-Un-debut-100.html">1</a>'
            '<a href="https://www.bedetheque.com/BD-Boys-Tome-2-La-suite-101.html">2</a></body></html>')
    source = Bedetheque.__new__(Bedetheque)
    source._series_details = {SERIES_PAGE: parser.parse_series_details(html)}
    assert source.series_by_address("https://www.bedetheque.com/BD-Boys-Tome-1-Un-debut-100.html") is None
    assert source.is_series_address(SERIES_PAGE) and not source.is_series_address("https://www.bedetheque.com/BD-Boys-Tome-1-Un-debut-100.html")
    key, name, albums = source.series_by_address(SERIES_PAGE)
    assert (key, name) == ("18658", "Boys") and [a.volume for a in albums] == [1, 2]
    proposal = review_batch.propose_for_series_address(source, SERIES_PAGE, ["/x/Boys T02.cbz", "/x/Boys T07.cbz"])
    assert [c.volume for c in proposal.mapping.values()] == [2] and proposal.total == 2


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


def test_a_tome_the_site_writes_with_a_minus_is_the_number_the_address_has():
    from bdnex.importer.bedetheque import parser

    html = ('<html><body><section class="bdt-ah"><h1><a href="https://www.bedetheque.com/serie-1-BD-x.html">Donjon Potron-Minet</a></h1>'
            '<h2 class="bdt-ah-sub">-82<span class="bdt-numa"></span>. Survivre aujourd\'hui</h2></section></body></html>')
    fields, _ = parser.parse_album_page(html, "https://www.bedetheque.com/BD-Donjon-Potron-Minet-Tome-82-Survivre-aujourd-hui-437533.html")
    info = parser.build_album_info(fields)
    assert info.volume == 82 and info.alternate_number is None and info.title == "Survivre aujourd'hui"


def test_the_address_does_not_number_an_author_miscellany_nor_give_a_filler_title():
    from bdnex.importer.bedetheque import parser

    def page(series, sub):
        return (f'<html><body><section class="bdt-ah"><h1><a href="https://www.bedetheque.com/serie-1-BD-x.html">{series}</a></h1>'
                f'<h2 class="bdt-ah-sub">{sub}</h2></section></body></html>')

    fields, _ = parser.parse_album_page(page("(AUT) Manara", '<span class="bdt-numa"></span>Le feu aux entrailles'),
                                        "https://www.bedetheque.com/BD-AUT-Manara-Tome-1992-Le-feu-aux-entrailles-35079.html")
    info = parser.build_album_info(fields)
    assert info.volume is None and info.series is None and info.title == "Le feu aux entrailles"
    fields, _ = parser.parse_album_page(page("Bob Morane (10 Intégrale)", '1<span class="bdt-numa"></span>. Integrale 1'),
                                        "https://www.bedetheque.com/BD-Bob-Morane-10-Integrale-Tome-1-Integrale-1-258688.html")
    info = parser.build_album_info(fields)
    assert info.volume == 1 and info.title == "Bob Morane (10 Intégrale)"  # "Integrale 1" is the site's filler: the series name stays the title


def test_a_reparse_removes_a_series_and_a_number_an_older_parser_gave_when_the_page_has_none(tmp_path):
    from bdnex.library import AlbumInfo, Library, SeriesInfo

    with Library(tmp_path / "l.sqlite") as lib:
        wrong = AlbumInfo("bedetheque", "1", "Mœbius", series=SeriesInfo("bedetheque", "s", "(AUT) Giraud"), volume=1984)
        album = lib.add_album(wrong)
        fresh = AlbumInfo("bedetheque", "1", "Mœbius")  # what the page says now: no series, no number
        assert any("volume" in change for change in lib.preview_merge(fresh))
        lib.merge_album(fresh)
        lib.prune_orphan_series()
        now = lib.get_album(album)
        assert now["volume"] is None and now["series"] is None and now["kind"] == "oneshot"
        # a sitemap-only parse (partial) knows less: it must not erase
        lib.add_album(AlbumInfo("bedetheque", "2", "Forge", series=SeriesInfo("bedetheque", "n", "Nains"), volume=2))
        lib.merge_album(AlbumInfo("bedetheque", "2", "Forge", extra={"partial": True}))
        kept = lib.get_album(lib.find_album("bedetheque", "2"))
        assert kept["volume"] == 2 and kept["series"] == "Nains"


def test_a_reparse_drops_the_special_an_older_parser_stored_next_to_a_number(tmp_path):
    from bdnex.library import AlbumInfo, Library, SeriesInfo

    with Library(tmp_path / "l.sqlite") as lib:
        series = SeriesInfo("bedetheque", "d", "Donjon Potron-Minet")
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Survivre aujourd'hui", series=series, volume=82, alternate_number="-82"))
        lib.merge_album(AlbumInfo("bedetheque", "1", "Survivre aujourd'hui", series=series, volume=82))
        now = lib.get_album(album)
        assert now["volume"] == 82 and now["alternate_number"] is None


def test_a_link_from_the_review_screen_asks_no_question(monkeypatch):
    """The screen owns the terminal: a cover question inside the import would wait for ever."""
    from bdnex.ui import import_cmd, verdicts

    seen = []
    monkeypatch.setattr(import_cmd, "run", lambda argv, out=None, configure_logging=True: seen.append(argv) or 0)
    assert verdicts.rematch("/x/a.cbz", "https://x/BD-1.html", verbosity=1)
    assert "--unattended" in seen[0] and "--database-only" in seen[0] and "-v" in seen[0]  # -v: the steps reach the log panel


def test_the_next_batch_does_not_repeat_the_files_of_the_batches_before(env):
    """A file passed over (s) or left without an album is not checked, but this run has shown it: the next batch is new files."""
    db, files = env

    async def script(app, pilot):
        first = [row.path for row in app.rows]
        assert first
        for _ in first:
            await pilot.press("s")  # every file skipped: nothing is saved, nothing is `checked`
        app.action_next_batch()
        await settle(app, pilot)
        assert not set(first) & {row.path for row in app.rows}

    drive(db, script)


def test_a_series_page_that_is_not_stored_is_fetched_with_the_real_fetcher(tmp_path, monkeypatch):
    """The screen's own source is offline and read-only: the series page is read apart, with the configured fetcher, and stored."""
    import yaml as _yaml

    import bdnex.importer.bedetheque as plugin
    from bdnex.config import load_config as _load

    conf = tmp_path / "bdnex.yaml"
    conf.write_text(_yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"), "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    html = '<a href="https://www.bedetheque.com/BD-Boys-Tome-1-Un-debut-100.html">1</a><a href="https://www.bedetheque.com/BD-Boys-Tome-2-La-suite-101.html">2</a>'
    asked = []
    monkeypatch.setattr(plugin, "build_fetcher", lambda settings: (lambda url: asked.append(url) or html.encode()))
    monkeypatch.setattr(plugin, "uses_saved_pages", lambda settings: False, raising=False)
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with Library(tmp_path / "lib.sqlite"):
        pass
    settings = _load().settings
    key, name, albums = batch.read_series_address(settings, SERIES_PAGE)
    assert (key, name) == ("18658", "Boys") and [a.volume for a in albums] == [1, 2] and asked == [SERIES_PAGE]
    batch.read_series_address(settings, SERIES_PAGE)
    assert asked == [SERIES_PAGE]  # the second time it is in the library


def test_ctrl_a_marks_every_undecided_file_and_again_none(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("m")
        assert len(app.marked) == len(app.rows)
        await pilot.press("m")
        assert not app.marked

    drive(db, script)


def test_the_log_panel_shows_bdnex_log_lines_and_an_import_s_output(env):
    import logging

    db, files = env

    async def script(app, pilot):
        logging.getLogger("bdnex.test").info("a page was read")
        sink = review_tui.LineSink(app)
        sink.write("fetching the album page...\npage read in 1.2s\n")
        await pilot.pause()
        text = "\n".join(line.text for line in app.query_one("#log").lines)
        assert "a page was read" in text and "page read in 1.2s" in text
        await pilot.press("v")
        assert app.query_one("#log").display is False

    drive(db, script)


def test_x_unmarks_everything(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("space")
        assert app.marked
        await pilot.press("x")
        assert not app.marked

    drive(db, script)


def test_a_series_page_without_the_files_volumes_says_what_each_side_has():
    from bdnex.importer.bedetheque import Bedetheque, parser

    html = '<a href="https://www.bedetheque.com/BD-Fell-2005-INT01-Feral-City-194887.html">intégrale</a>'
    source = Bedetheque.__new__(Bedetheque)
    source._series_details = {"https://www.bedetheque.com/serie-9-BD-Fell.html": parser.parse_series_details(html)}
    with pytest.raises(batch.NoVolumeFound) as err:
        batch.propose_for_series_address(source, "https://www.bedetheque.com/serie-9-BD-Fell.html", ["/x/Fell 02 (2005).cbz", "/x/Fell 03 (2005).cbz"])
    assert "none with a volume number" in str(err.value) and "ask for 2 and 3" in str(err.value)


def test_a_series_page_of_collected_editions_still_lists_its_albums():
    from bdnex.importer.bedetheque import parser

    html = ('<a href="https://www.bedetheque.com/BD-Fell-2005-INT01-Feral-City-194887.html">Feral City</a>'
            '<a href="https://www.bedetheque.com/BD-Batman-Tome-1-X-5.html">another series</a>')
    assert [a["source_id"] for a in parser.parse_series_details(html)["albums"]] == ["5"]  # without the name, the other series wins
    albums = parser.parse_series_details(html, "Fell")["albums"]
    assert [a["source_id"] for a in albums] == ["194887"] and albums[0]["volume"] is None


def test_e_writes_the_metadata_of_a_file_by_hand_and_links_it(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("e")  # a file with an album of the site: the form of its metadata (the series name included)
        await settle(app, pilot)
        assert app.screen.__class__.__name__ == "EditScreen" and app.screen.query("#f-series")
        await pilot.press("escape")  # closed unchanged
        await settle(app, pilot)
        await pilot.press("u")  # unlinked first, then written by hand
        await pilot.press("up")
        await pilot.press("e")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ == "EditScreen"
        app.screen.query_one("#f-series").value = "Fell"
        app.screen.query_one("#f-volume").value = "2"
        app.screen.query_one("#f-publisher").value = "Image"
        await pilot.press("ctrl+s")
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert result["cells"][0][7] == "linked" or result["cells"][1][7] == "linked"
    with Library(db) as lib:
        albums = [lib.get_album(lib.get_file(p)["album_id"]) for p in files.values()]
    assert any(a["source"] == "manual" and a["series"] == "Fell" and a["volume"] == 2 for a in albums)


def test_e_with_several_marked_files_writes_a_series(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("m")
        await pilot.press("e")
        await settle(app, pilot)
        screen = app.screen
        assert screen.__class__.__name__ == "EditScreen" and screen.volumes is not None
        screen.query_one("#f-series").value = "Nains Bis"
        await pilot.press("ctrl+s")
        await settle(app, pilot)

    result, _ = drive(db, script)
    with Library(db) as lib:
        series = {lib.get_album(lib.get_file(p)["album_id"])["series"] for p in files.values()}
    assert series == {"Nains Bis"} and all(row[7] == "linked" for row in result["cells"])


def test_the_title_says_which_batch_of_about_how_many(env):
    db, files = env

    async def script(app, pilot):
        assert app.first_total == 3 and "batch 1/1" in app.sub_title  # three files to check (two doubtful, one without album), 50 a batch

    drive(db, script, size=50)

    async def small(app, pilot):
        assert "batch 1/3" in app.sub_title  # a batch of one file: three batches

    drive(db, small, size=1)


def test_a_file_the_import_skipped_has_no_record_and_gets_one_with_its_link(tmp_path, make_cbz):
    from bdnex.importer.source import Candidate

    lib = Library(":memory:")
    path = make_cbz("Feuille de chou.cbz")
    assert lib.get_file(path) is None
    row = batch.Row(path, "OneShot/Feuille", None, None, None, 65.0, None, "unmatched", [], None)
    candidate = Candidate("416850", "https://www.bedetheque.com/BD-Feuille-de-chou-INT-Gainsbourg-416850.html", "feuille", None)
    album_id = batch.link_now(lib, row, candidate)
    assert lib.get_file(path)["album_id"] == album_id and row.status == "linked" and row.file_id is not None
    assert lib.get_file(path)["md5"].startswith("unread:")  # not read: a big file on a network disk would take seconds


def test_a_log_line_from_a_thread_holding_the_write_lock_does_not_freeze_the_screen(env):
    """A background import logs while it is in a write transaction; the screen then writes a link. The log line must not wait for
    the screen (it used to: both waited for each other until the database's 30 s timeout)."""
    import time

    db, files = env
    holding, done = threading.Event(), threading.Event()
    seen = {}

    def background(app):
        with Library(db) as lib:
            lib.conn.execute("BEGIN IMMEDIATE")
            holding.set()
            app.log_from_anywhere("I fetch a page while I hold the write lock")
            seen["logged"] = True
            time.sleep(0.3)
            lib.conn.commit()
        done.set()

    async def script(app, pilot):
        thread = threading.Thread(target=background, args=(app,))
        thread.start()
        holding.wait(5)
        started = time.monotonic()
        app.link(app.rows[0], FORGE.url, FORGE)  # the screen's own write: waits for the lock, which the thread frees at once
        seen["took"] = time.monotonic() - started
        thread.join(5)

    drive(db, script)
    assert seen.get("logged") and seen["took"] < 5



def _edit_screen_script(then):
    async def script(app, pilot):
        await pilot.press("u")
        await pilot.press("up")
        await pilot.press("e")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ == "EditScreen"
        app.screen.query_one("#f-series").value = "Fell"
        await then(app, pilot)
    return script


def test_edit_form_ok_button_saves(env):
    db, files = env

    async def then(app, pilot):
        await pilot.click("#ok")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ != "EditScreen"

    drive(db, _edit_screen_script(then), rows=120)  # the hand-written form has many fields now: taller, so OK stays on screen
    with Library(db) as lib:
        assert any((lib.get_album(lib.get_file(p)["album_id"]) or {}).get("series") == "Fell" for p in files.values())


def test_edit_form_cancel_button_and_x_outside_a_text_field(env):
    db, files = env

    async def cancel(app, pilot):
        await pilot.click("#cancel")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ != "EditScreen"

    async def x_in_field(app, pilot):
        await pilot.press("x")  # typed into the focused field, not a cancel
        assert app.screen.__class__.__name__ == "EditScreen"
        assert app.screen.query_one("#f-series").value.endswith("x")
        app.screen.set_focus(app.screen.query_one("#ok"))
        await pilot.press("x")
        await settle(app, pilot)
        assert app.screen.__class__.__name__ != "EditScreen"

    drive(db, _edit_screen_script(cancel), rows=120)  # the hand-written form has many fields now: taller, so Cancel/OK stay on screen
    drive(db, _edit_screen_script(x_in_field))  # keyboard-driven: no click, so the default screen size is fine
    with Library(db) as lib:
        assert not any((lib.get_album(lib.get_file(p)["album_id"]) or {}).get("series") == "Fell" for p in files.values()
                       if lib.get_file(p)["album_id"])


def test_a_quality_tag_glued_to_the_number_is_not_part_of_it():
    from bdnex.importer.folders import title_and_volume
    from bdnex.importer.matching import volumes_for

    for name, volume in (("Death_Note_-_003lq.cbz", 3), ("Death_Note-035lq.cbz", 35), ("Death_Note_-_010HQ.cbz", 10)):
        path = "/x/Death Note/" + name
        assert title_and_volume(path) == (["death", "note"], volume) and volumes_for(path) == [volume]


def test_files_numbered_past_the_last_volume_are_chapters_and_are_not_linked_by_number():
    import pytest

    albums = [Candidate(str(n), f"https://www.bedetheque.com/BD-Death-Note-Tome-{n}-{n}.html", "dn", n) for n in range(1, 13)]
    paths = [f"/x/Death Note/Death_Note_-_{n:03d}lq.cbz" for n in range(3, 43)]
    with pytest.raises(batch.NoVolumeFound, match="chapters"):
        batch.propose_for_series_address(None, "https://www.bedetheque.com/serie-1-BD-Death-Note.html", paths, found=("1", "Death Note", albums))
    ok = batch.propose_for_series_address(None, "x", [f"/x/Death Note/Death Note T{n:02d}.cbz" for n in range(1, 13)],
                                          found=("1", "Death Note", albums))
    assert len(ok.mapping) == 12


def test_i_with_a_bdtheque_address_links_the_file_to_what_that_page_says(env, monkeypatch):
    from bdnex.importer import bdtheque

    db, files = env
    page = open(os.path.join(os.path.dirname(__file__), "bdtheque_series_14922.html"), encoding="utf-8").read()
    asked = []

    def read(settings, address, path):
        asked.append((address, path))
        return bdtheque.album_info(bdtheque.parse_series_page(page))

    monkeypatch.setattr(batch, "read_bdtheque", read)

    async def script(app, pilot):
        await pilot.press("i")
        await settle(app, pilot)
        app.screen.query_one("Input").value = "https://www.bdtheque.com/series/14922/le-pere-des-etoiles-pere-ou-impairs"
        await pilot.press("enter")
        await settle(app, pilot)
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert asked and result["cells"][0][7] == "linked"
    with Library(db) as lib:
        albums = [lib.get_album(lib.get_file(p)["album_id"]) for p in files.values() if lib.get_file(p)["album_id"]]
    assert any(a["source"] == "bdtheque" and a["publisher"] == "Dargaud" for a in albums)


def test_the_state_column_turns_a_wheel_while_a_page_is_fetched(env, monkeypatch):
    from bdnex.importer import bdtheque

    db, files = env
    page = open(os.path.join(os.path.dirname(__file__), "bdtheque_series_14922.html"), encoding="utf-8").read()
    release = threading.Event()
    seen = {}

    def read(settings, address, path):
        release.wait(5)
        return bdtheque.album_info(bdtheque.parse_series_page(page))

    monkeypatch.setattr(batch, "read_bdtheque", read)

    async def script(app, pilot):
        await pilot.press("i")
        await settle(app, pilot)
        app.screen.query_one("Input").value = "https://www.bdtheque.com/series/14922/x"
        await pilot.press("enter")
        await pilot.pause(0.4)
        seen["during"] = [str(c) for c in app.query_one("DataTable").get_row_at(0)][7]
        seen["later"] = None
        await pilot.pause(0.4)
        seen["frames"] = {str(app.query_one("DataTable").get_row_at(0)[7])[:1], seen["during"][:1]}
        release.set()
        await fetched(app, pilot)
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert "fetching the page" in seen["during"] and seen["during"][0] in review_tui.SPINNER
    assert len(seen["frames"]) >= 1 and result["cells"][0][7] == "linked"


def test_no_question_about_a_next_batch_when_there_is_none(env):
    """Three files, a batch of fifty: once they are decided the screen says so; it does not ask for a next batch."""
    db, files = env
    seen = {}

    async def script(app, pilot):
        for _ in range(3):
            await pilot.press("s")  # a file passed over counts as decided for the screen
            await settle(app, pilot)
        seen["screen"] = app.screen.__class__.__name__
        seen["total"], seen["rows"] = app.batch_total, len(app.rows)

    drive(db, script)
    assert seen["total"] <= seen["rows"] and seen["screen"] != "AskScreen"


def test_a_failing_fetch_does_not_stop_the_next_ones_and_quitting_waits_for_them(env, monkeypatch):
    """A corrupt file's import fails: the files queued after it are still fetched, also when the screen is closed meanwhile."""
    import io

    from bdnex.ui import verdicts

    db, files = env
    ran = []

    def rematch(path, url, keep_both=False, verbosity=0):
        ran.append(os.path.basename(path))
        if len(ran) == 1:
            print("failed: can not be opened: Corrupt input data")
            return False
        threading.Event().wait(0.3)
        return True

    monkeypatch.setattr(verdicts, "rematch", rematch)

    async def script(app, pilot):
        app.finish_fetches = True  # what `run` sets: closing the screen does not cancel the queue
        await pilot.press("space")
        await pilot.press("up")
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)

    result, _ = drive(db, script)
    out = io.StringIO()
    left = result["app"].finish_pending_fetches(out)
    assert left == 0 and len(ran) >= 2, ran


def test_a_one_shot_picked_with_a_is_linked_to_the_one_file_instead_of_a_refusal(env, monkeypatch):
    """`a` on one file whose album has no volume (a one-shot, `OS`): the album picked is linked, as `o` would."""
    db, files = env
    release = threading.Event()
    release.set()
    seen_calls.clear()
    _linking(monkeypatch, db, release)
    monkeypatch.setattr(batch, "propose_for_series", lambda source, candidate, paths: None)

    async def script(app, pilot):
        await pilot.press("space")
        await pilot.press("up")
        await pilot.press("a")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert result["cells"][0][7] == "linked" and seen_calls == [True]


def test_a_one_shot_series_page_gives_its_only_album_to_the_one_file_without_a_volume():
    from bdnex.importer.bedetheque import Bedetheque, parser

    html = '<a href="https://www.bedetheque.com/BD-Scalp-2005-INT01-Scalp-194887.html">Scalp</a>'
    address = "https://www.bedetheque.com/serie-9-BD-Scalp.html"
    source = Bedetheque.__new__(Bedetheque)
    source._series_details = {address: parser.parse_series_details(html, "Scalp")}
    found = batch.propose_for_series_address(source, address, ["/x/Scalp.cbz"])
    assert list(found.mapping) == ["/x/Scalp.cbz"]
    with pytest.raises(batch.NoVolumeFound):  # two files: which one is it? still refused
        batch.propose_for_series_address(source, address, ["/x/Scalp.cbz", "/x/Scalp bis.cbz"])


def test_a_volume_that_only_looks_like_a_digit_is_refused_not_a_crash(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        await pilot.press("m")
        await pilot.press("e")
        await settle(app, pilot)
        screen = app.screen
        screen.query_one("#f-series").value = "Nains Bis"
        screen.query_one("#volumes").text = "²\n1\n2"
        await pilot.press("ctrl+s")
        await settle(app, pilot)
        seen["still"] = app.screen is screen
        await pilot.press("escape")

    drive(db, script)
    assert seen["still"]


def test_a_failed_link_is_reported_on_the_row_and_does_not_close_the_screen(env, monkeypatch):
    db, files = env
    release = threading.Event()
    release.set()
    _linking(monkeypatch, db, release)

    def broken(library, row, candidate):
        raise OSError("the disk is gone")

    monkeypatch.setattr(batch, "link_now", broken)

    async def script(app, pilot):
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.is_running

    result, _ = drive(db, script)
    assert result["cells"][0][7] != "linked"


def test_a_series_link_leaves_a_row_decided_meanwhile_alone(env, monkeypatch):
    db, files = env
    release = threading.Event()
    release.set()
    seen_calls.clear()
    _linking(monkeypatch, db, release)

    async def script(app, pilot):
        first, second = app.rows[0], app.rows[1]
        second.status = "unlinked"  # decided while the question was open
        candidate = FORGE
        app.link_many([(first, candidate), (second, candidate)])
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert result["app"].rows[1].status == "unlinked" and len(seen_calls) == 1


def test_the_picker_says_in_a_column_whether_a_hit_is_a_series_or_one_album():
    serie = Candidate("18658", SERIES_PAGE, "BD Boys", None)
    for series_ok, expect in ((True, ("SERIES", "album")), (False, ())):
        screen = review_tui.FuzzyScreen(None, "", "x", series_ok=series_ok)
        a, b = screen.hit_line(serie, 90.0).plain, screen.hit_line(FORGE, 80.0).plain
        assert all(word in text for word, text in zip(expect, (a, b)))
        if not series_ok:
            assert "SERIES" not in a and "album" not in b
    assert screen.hit_line(serie, 90.0).plain.strip().endswith("BD Boys")


async def _ready(app, pilot, what, seconds=10):
    """Wait for the next batch to be weighed in the background (a plain thread: `settle` does not know it)."""
    for _ in range(int(seconds / 0.05)):
        if what(app):
            return
        await asyncio.sleep(0.05)
        await pilot.pause()
    raise AssertionError("the next batch was not prepared")


def test_the_next_batch_is_weighed_while_the_first_is_looked_at_and_n_shows_it_at_once(env, monkeypatch):
    db, files = env
    weighed = []
    real = batch.next_batch

    def counting(*args, **kwargs):
        weighed.append(len(args[7]) if len(args) > 7 else 0)  # how many files were already shown
        return real(*args, **kwargs)

    monkeypatch.setattr(batch, "next_batch", counting)
    seen = {}

    async def script(app, pilot):
        await _ready(app, pilot, lambda a: a.ahead is not None)
        first = app.rows[0].path
        seen["prepared"] = [r.path for r in app.ahead[0]]
        before = len(weighed)
        await pilot.press("n")
        await pilot.pause()
        seen["shown"] = [r.path for r in app.rows]
        seen["weighed_again"] = len(weighed) - before
        seen["first"] = first

    drive(db, script, unmatched=True, size=1)
    assert seen["prepared"] == seen["shown"] and seen["first"] not in seen["shown"]
    assert seen["weighed_again"] == 0 or seen["weighed_again"] == 1  # a batch after it may be prepared, never the same one twice


def test_b_goes_back_to_the_batch_before_with_its_decisions_and_n_forward_again(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        await _ready(app, pilot, lambda a: a.ahead is not None)
        first = app.rows[0]
        await pilot.press("k")  # decided in batch 1; batch 2 is asked for
        await pilot.pause()
        await pilot.press("w")  # (the question waits in the queue)
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        seen["second"] = app.number
        await pilot.press("b")
        await pilot.pause()
        seen["back"] = (app.number, app.rows[0] is first, app.rows[0].status)
        await pilot.press("b")  # already the first
        await pilot.pause()
        seen["still"] = app.number
        await pilot.press("n")
        await pilot.pause()
        seen["forward"] = app.number

    drive(db, script, unmatched=True, size=1)
    assert seen["second"] == 2 and seen["back"] == (1, True, "keep")
    assert seen["still"] == 1 and seen["forward"] == 2


def test_n_before_the_batch_is_ready_waits_for_it_and_shows_it(env, monkeypatch):
    db, files = env
    import threading as _t
    gate = _t.Event()
    real = batch.next_batch
    calls = []

    def slow(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:  # the batch weighed ahead
            gate.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(batch, "next_batch", slow)
    seen = {}

    async def script(app, pilot):
        await pilot.press("n")  # the next batch is still being weighed
        await pilot.pause()
        seen["loading"] = app.loading
        gate.set()
        await _ready(app, pilot, lambda a: not a.loading)
        seen["number"] = app.number

    drive(db, script, unmatched=True, size=1)
    assert seen["loading"] and seen["number"] == 2 and len(calls) <= 3


def test_a_fetch_for_the_review_screen_does_not_reconfigure_the_logging(monkeypatch):
    from bdnex.ui import import_cmd, verdicts

    seen = {}
    monkeypatch.setattr(import_cmd, "run", lambda argv, out=None, configure_logging=True: seen.update(configure=configure_logging) or 0)
    assert verdicts.rematch("/x/a.cbz", "https://www.bedetheque.com/BD-Nains-Forge-2.html") is True
    assert seen["configure"] is False
    calls = []
    monkeypatch.undo()
    monkeypatch.setattr(import_cmd, "setup_logging", lambda verbosity: calls.append(verbosity))
    for configure in (False, True):
        with contextlib.suppress(SystemExit, Exception):
            import_cmd.run(["-n", "/nonexistent/a.cbz"], out=io.StringIO(), configure_logging=configure)
    assert calls == [0]  # only the run that was asked to set the logging up did


def test_the_wait_message_after_the_screen_closes_goes_to_the_terminal_not_to_a_redirected_stderr(env, monkeypatch):
    db, files = env
    from concurrent.futures import ThreadPoolExecutor

    app = review_tui.ReviewApp(Library(db), load_config().settings, FakeSource(), 50, (), True)
    terminal, redirected = io.StringIO(), io.StringIO()
    assert app.terminal_stderr is sys.stderr
    app.terminal_stderr = terminal
    app.fetcher, app.remaining = ThreadPoolExecutor(max_workers=1), 1
    threading.Timer(0.7, lambda: setattr(app, "remaining", 0)).start()
    monkeypatch.setattr(sys, "stderr", redirected)  # a fetch still running has redirected it
    app.finish_pending_fetches()
    assert "Fetching the page and cover of 1 linked file(s)" in terminal.getvalue() and redirected.getvalue() == ""


def test_what_an_import_prints_in_the_fetch_thread_goes_to_the_panel_and_is_not_kept(env):
    db, files = env
    lines = []
    app = type("App", (), {"log_from_anywhere": lambda self, text: lines.append(text)})()
    sink = review_tui.LineSink(app)
    for number in range(1000):
        sink.write(f"line {number}\n")
    sink.write("partial")
    sink.write(" line\r")
    assert lines[0] == "line 0" and lines[-1] == "partial line" and len(lines) == 1001
    assert sink.getvalue() == ""  # nothing accumulates for the whole session


def test_t_lists_the_candidates_of_the_file_and_enter_links_the_highlighted_one(env, monkeypatch):
    db, files = env
    release = threading.Event()
    release.set()
    _linking(monkeypatch, db, release)
    seen = {}

    async def script(app, pilot):
        await pilot.press("t")
        await settle(app, pilot)
        screen = app.screen
        seen["screen"] = screen.__class__.__name__
        seen["found"] = [(c.source_id, entry["score"]) for c, entry in screen.found]
        await pilot.press("enter")
        await pilot.pause()
        seen["status"] = app.rows[0].status

    result, source = drive(db, script)
    assert seen["screen"] == "CandidatesScreen" and seen["found"] == [("2", 91)]  # the source's own best guesses for this file
    assert seen["status"] == "linked"
    with Library(db) as lib:
        assert lib.get_album(lib.get_file(files["wrong"])["album_id"])["source_id"] == "2"


def test_o_from_the_candidates_goes_on_to_the_search_of_every_album(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        await pilot.press("t")
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)
        seen["screen"] = app.screen.__class__.__name__
        await pilot.press("escape")

    drive(db, script)
    assert seen["screen"] == "FuzzyScreen"


def test_a_candidate_line_says_the_score_the_current_album_the_year_and_what_the_evidence_says():
    entry = {"score": 96, "album": "Bob Morane 04 (Lefrancq) - T01 - L Oiseau de Feu", "year": 1989, "publisher": "Lefrancq",
             "complete": False, "current": True, "verdict": "unverified", "why_score": [], "for": [], "against": []}
    text = review_tui.CandidatesScreen.line(entry).plain
    assert text.startswith(" 96  * Bob Morane 04") and "1989 Lefrancq" in text and text.endswith("[unverified, page not read]")
    assert "* " not in review_tui.CandidatesScreen.line({**entry, "current": False}).plain[:6]


def test_the_candidates_of_a_file_are_the_matchers_ranked_list_with_its_evidence(tmp_path, make_cbz):
    import types

    from bdnex.importer.bedetheque import Bedetheque
    from helpers import FakeWeb

    library = Library(":memory:")
    source = Bedetheque(library, fetch=FakeWeb(), sleep=lambda s: None)
    source.update_sitemaps()
    path = make_cbz("Nains - T01 - Redwin de la forge (Smith) [1950].cbz", directory=str(tmp_path))
    found = review_tui.candidates_of(types.SimpleNamespace(source=source), path, None)
    assert found and found[0][0].source_id == "245127"
    entry = found[0][1]
    assert entry["score"] > 60 and entry["why_score"] and entry["verdict"] and entry["url"] == found[0][0].url
    library.close()


def test_with_several_endpoints_several_rows_show_fetching_at_the_same_time(env, monkeypatch):
    from bdnex.importer.bedetheque import Bedetheque
    from bdnex.ui.review_fetch import DetailsQueue

    db, files = env
    release = threading.Event()  # the imports wait for it: both rows are still being fetched when they are looked at
    _linking(monkeypatch, db, release, outcome="keep")
    barrier = threading.Barrier(2, timeout=10)  # a request goes on only when two are being made at the same time
    web = lambda url: (barrier.wait(), b"<html>page</html>")[1]  # noqa: E731
    seen = {}

    async def script(app, pilot):
        app.fetcher = DetailsQueue(app.settings, lambda library: Bedetheque(library, fetch=web, delay=(0, 0), sleep=lambda s: None, parallel=2),
                                   workers=2)
        first, second = app.rows[0], app.rows[1]
        for number, row in enumerate((first, second), start=1):
            row.pending, row.queued_at = True, time.monotonic()
            app.queue_details(row, f"https://www.bedetheque.com/BD-Serie-Tome-{number}-Titre-{1000 + number}.html", row.link_id)
        for _ in range(200):
            if first.started_at and second.started_at:
                break
            await asyncio.sleep(0.05)
        app.tick_pending()
        seen["both"] = [str(app.query_one("DataTable").get_row_at(i)[7]) for i in (0, 1)]
        release.set()
        for _ in range(200):
            await asyncio.sleep(0.05)
            await pilot.pause()
            if app.remaining == 0:
                break
        seen["left"] = app.remaining

    drive(db, script)
    assert all("fetching" in text for text in seen["both"]), seen["both"]  # both spin: two downloads are going on at once
    assert seen["left"] == 0


def test_a_without_a_mark_is_for_the_file_under_the_cursor_not_for_the_rest_of_its_folder(env, monkeypatch):
    """One-shots (Batman - Killing Joke, ...) share a folder and have no volume: `a` used to take every undecided file of the folder."""
    db, files = env
    release = threading.Event()
    release.set()
    _linking(monkeypatch, db, release)
    seen = {}

    async def script(app, pilot):
        assert len({r.folder for r in app.rows}) == 1 and len(app.rows) == 3  # all three files share a folder, none is marked
        await pilot.press("a")
        await settle(app, pilot)
        seen["heading"], seen["about"] = app.screen.heading, app.screen.about
        await pilot.press("enter")  # one file and one album picked: that album is linked
        await settle(app, pilot)

    result, _ = drive(db, script)
    assert "Pick this file's album, or a SERIES" in seen["heading"]
    assert len(seen["about"]) == 1 and seen["about"][0].endswith("/" + os.path.basename(files["wrong"]))  # the folder and the name, to search with
    assert [row[7] for row in result["cells"]].count("linked") == 1  # only the file under the cursor


def test_a_works_on_a_file_already_decided_so_a_wrong_keep_can_be_matched_again(env, monkeypatch):
    db, files = env

    async def script(app, pilot):
        await pilot.press("k")  # decided: kept
        await pilot.press("up")
        row = app.current()
        assert row.status == "keep"
        assert app.files_to_attach() == [row]  # still the file under the cursor: it can be given another album

    drive(db, script, size=50)


def test_m_then_a_is_for_the_marked_files_and_the_heading_says_how_many(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        await pilot.press("m")
        await pilot.press("a")
        await settle(app, pilot)
        seen["heading"] = app.screen.heading
        await pilot.press("escape")

    drive(db, script)
    assert "3 marked files" in seen["heading"]


def test_the_search_screens_show_the_folder_and_name_of_the_file_they_are_for(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        await pilot.press("m")
        await pilot.press("a")
        await settle(app, pilot)
        seen["several"] = app.screen.about
        await pilot.press("escape")
        await pilot.press("x")  # no marks
        await pilot.press("o")
        await settle(app, pilot)
        seen["one"] = (app.screen.about, bool(app.screen.query("#about")))  # and the label is on the screen
        await pilot.press("escape")

    drive(db, script)
    assert len(seen["several"]) == 3 and all("/" in line for line in seen["several"])
    assert len(seen["one"][0]) == 1 and seen["one"][1] is True


def test_the_series_and_album_ticks_each_search_their_own_kind(env, monkeypatch):
    """Many series with the name must not push every album out of the list: each kind is searched on its own."""
    db, files = env
    asked = []

    def fake(hub, text, limit=60, series=False, albums=True):
        asked.append((series, albums))
        return [], 0

    monkeypatch.setattr(review_tui, "fuzzy_search", fake)
    seen = {}

    async def script(app, pilot):
        await pilot.press("a")
        await settle(app, pilot)
        await pilot.press("f2")  # series off: albums only
        await settle(app, pilot)
        await pilot.press("f2")  # series back on
        await pilot.press("f3")  # albums off: series only
        await settle(app, pilot)
        await pilot.press("f2")  # both off: nothing is asked for
        await settle(app, pilot)
        seen["ticks"] = (app.screen.show_series, app.screen.show_albums)
        await pilot.press("escape")

    drive(db, script)
    assert (True, True) in asked and (False, True) in asked and (True, False) in asked
    assert seen["ticks"] == (False, False) and asked[-1] != (False, False)  # both off: no search was made for that state


def test_volume_numbers_are_said_in_words():
    assert batch.span([1, 2, 3, 4]) == "1 to 4" and batch.span([5, 6]) == "5 and 6" and batch.span([5]) == "5"
    assert batch.span([1, 3, 7, 8, 9]) == "1, 3 and 7 to 9" and batch.span([9, 3, 1, 3]) == "1, 3 and 9"


def _bill_baroud_page():
    from bdnex.importer.bedetheque import Bedetheque, parser

    html = "".join(f'<a href="https://www.bedetheque.com/BD-Bill-Baroud-Tome-{n}-Titre-{n}-{6000 + n}.html">x</a>' for n in range(1, 5))
    html += '<a href="https://www.bedetheque.com/BD-Bill-Baroud-INT-L-edition-integrale-110371.html">x</a>'
    address = "https://www.bedetheque.com/serie-1-BD-Bill-Baroud.html"
    source = Bedetheque.__new__(Bedetheque)
    source._series_details = {address: parser.parse_series_details(html, "Bill-Baroud")}
    return source, address


def test_a_series_page_whose_volumes_none_of_the_files_ask_for_says_so_in_words_and_marks_it():
    source, address = _bill_baroud_page()
    paths = [f"/x/Bill Baroud #{n:03d} - Titre.cbr" for n in range(5, 10)]
    with pytest.raises(batch.NoVolumeFound) as err:
        batch.propose_for_series_address(source, address, paths)
    assert "the page lists volume(s) 1 to 4; these 5 file(s) ask for 5 to 9" in str(err.value) and "does not have" in str(err.value)
    assert err.value.none_matched and err.value.name == "Bill Baroud"  # the screen offers to write them by hand
    with pytest.raises(batch.NoVolumeFound) as chapters:  # some files do find their volume: the chapter guard, no such offer
        batch.propose_for_series_address(source, address, ["/x/Bill Baroud #001.cbr", *paths])
    assert not chapters.value.none_matched


def _no_volume_fits(paths, **kw):
    raise batch.NoVolumeFound(f"Bill Baroud: the page lists volume(s) 1 to 4; these {len(paths)} file(s) ask for 5 to 7. They look like "
                              "another series than this page's, or one the site does not have.", name="Bill Baroud", none_matched=True)


def test_when_the_series_page_does_not_fit_the_screen_says_so_and_offers_to_write_the_files_by_hand(env, monkeypatch):
    db, files = env
    monkeypatch.setattr(batch, "read_series_address", lambda settings, text: None)
    monkeypatch.setattr(batch, "propose_for_series_address", lambda source, text, paths, found=None: _no_volume_fits(paths))
    seen = {}

    async def script(app, pilot):
        await pilot.press("m")
        await pilot.press("a")
        await settle(app, pilot)
        app.screen.query_one("#words").value = SERIES_PAGE
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("w")  # the offer waits in the queue: `w` opens it
        await pilot.pause()
        seen["screen"], seen["question"] = app.screen.__class__.__name__, app.screen.question
        await pilot.press("y")
        await settle(app, pilot)
        seen["form"], seen["series"] = app.screen.__class__.__name__, app.screen.query_one("#f-series").value
        await pilot.press("escape")

    drive(db, script)
    assert seen["screen"] == "AskScreen" and "the page lists volume(s) 1 to 4" in seen["question"]  # what is going on, in the popup
    assert "Write these 3 files by hand as the series 'Bill Baroud'" in seen["question"]
    assert seen["form"] == "EditScreen" and seen["series"] == "Bill Baroud"  # the form opens with the page's series name


def test_declining_the_offer_changes_nothing(env, monkeypatch):
    db, files = env
    monkeypatch.setattr(batch, "read_series_address", lambda settings, text: None)
    monkeypatch.setattr(batch, "propose_for_series_address", lambda source, text, paths, found=None: _no_volume_fits(paths))
    seen = {}

    async def script(app, pilot):
        await pilot.press("m")
        await pilot.press("a")
        await settle(app, pilot)
        app.screen.query_one("#words").value = SERIES_PAGE
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("n")  # no
        await settle(app, pilot)
        seen["still_asking"] = app.screen.__class__.__name__ in ("AskScreen", "EditScreen")

    result, _ = drive(db, script)
    assert seen["still_asking"] is False and all(cell[7] != "linked" for cell in result["cells"])
