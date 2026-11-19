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
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin", series=series, volume=1, year=2015))
        other = lib.add_album(AlbumInfo("bedetheque", "2", "Forge", series=series, volume=2, year=2016))
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
    assert first[1] == "Nains - T05 - Autre.cbz" and "Redwin" in first[3] and first[4] == "78%" and first[5] == "-"
    assert third[3] == "(no album)" and third[1] == "Loose file.cbz"  # the unmatched is shown, last
    assert [first[6], second[6], third[6]] == ["contradicted", "unconfirmed", "no album"]  # what is wrong, until it is decided


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


def test_keys_decide_and_each_decision_is_saved_at_once(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("u")  # the contradicted one: unlink
        await pilot.press("k")  # the unconfirmed one: keep
        await pilot.press("l")  # the file without album: leave it

    result, _ = drive(db, script)
    assert [row[6] for row in result["cells"]] == ["unlinked", "keep", "left"]
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
    assert result["cells"][0][6] == "skipped"
    with Library(db) as lib:
        assert [r.path for r in batch.next_batch(lib, load_config().settings, 50)][0] == files["wrong"]


def test_a_file_without_album_cannot_be_kept_or_unlinked(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("down", "down")
        await pilot.press("k")
        await pilot.press("u")

    result, _ = drive(db, script)
    assert result["cells"][2][6] == "no album" and result["app"].decided == {}  # nothing was decided, nothing saved


def _linking(monkeypatch, db, release, outcome="switch"):
    """What `import --album --database-only` does to the library, on its own connection (it runs in a thread), held until
    ``release`` is set so that the test can look at the table while the link is being made."""
    from bdnex.ui import verdicts

    def rematch(path, url):
        release.wait(5)
        if outcome == "switch":
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
        seen["busy"] = (row.busy, str(app.query_one("DataTable").get_row_at(0)[6]), row.message)  # while it is being made
        await pilot.press("k")  # a decision on a file that is being linked is refused
        assert not row.status
        release.set()
        await settle(app, pilot)

    result, source = drive(db, script)
    assert seen["busy"][0] is True and seen["busy"][1] == "linking..." and "reading the album page" in seen["busy"][2]
    first = result["cells"][0]
    assert first[6] == "linked" and "Forge" in first[3] and first[4] == "you" and first[5] == "66%"  # the row says what happened
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
    assert result["cells"][0][6] == "refused" and "refused" in result["app"].rows[0].message
    with Library(db) as lib:
        assert lib.get_file(files["wrong"])["album_id"] is not None  # nothing changed


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
    assert result["cells"][0][6] == "linked"


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
    assert result["cells"][0][6] == "linked"


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
