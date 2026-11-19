"""`bdnex review --batch`: the Textual table, driven with Textual's pilot (no terminal needed)."""

import asyncio

import pytest
import yaml

pytest.importorskip("textual")

from bdnex.config import load_config  # noqa: E402
from bdnex.importer.source import Candidate  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402
from bdnex.library import review_batch as batch  # noqa: E402
from bdnex.ui import review_tui  # noqa: E402


class FakeSource:
    name = "bedetheque"

    def __init__(self):
        self.asked = []

    def candidates(self, text, limit=5):
        self.asked.append(text)
        return [(Candidate("2", "https://www.bedetheque.com/BD-Forge-2.html", "forge", 2), 91.0)]

    def candidate_for(self, text):
        return Candidate("2", "https://www.bedetheque.com/BD-Forge-2.html", "forge", 2) if "Forge" in text or text == "2" else None


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


def drive(db, script, unmatched=True, size=50):
    """Run the app, call ``script(app, pilot)``, and return the app."""
    settings = load_config().settings
    source = FakeSource()
    result = {}

    async def go():
        with Library(db) as lib:
            app = review_tui.ReviewApp(lib, settings, source, size, (), unmatched)
            async with app.run_test(size=(170, 45)) as pilot:
                await pilot.pause()
                await script(app, pilot)
                result["app"] = app
                result["cells"] = [[str(cell) for cell in app.query_one("DataTable").get_row_at(i)] for i in range(len(app.rows))]

    asyncio.run(go())
    return result, source


def test_the_table_shows_file_folder_album_scores_and_status(env):
    db, files = env

    async def script(app, pilot):
        assert app.query_one("DataTable").row_count == 3

    result, _ = drive(db, script)
    first, second, third = result["cells"]
    assert first[1] == "Nains - T05 - Autre.cbz" and "Redwin" in first[3] and first[4] == "78%" and first[5] == "-"
    assert third[3] == "(no album)" and third[1] == "Loose file.cbz"  # the unmatched is shown, last
    assert [first[6], second[6], third[6]] == ["contradicted", "unconfirmed", "no album"]  # what is wrong, until it is decided


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


def test_o_searches_the_album_list_and_the_pick_links_the_file(env, monkeypatch):
    from bdnex.ui import verdicts

    db, files = env

    def switch(path, url):  # what `import --album --database-only` does, on its own connection (it runs in a thread)
        with Library(db) as lib:
            lib.set_file_album(path, lib.find_album("bedetheque", "2"))
        return True

    monkeypatch.setattr(verdicts, "rematch", switch)

    async def script(app, pilot):
        await pilot.press("o")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "SearchScreen"
        await pilot.press("enter")  # the first hit
        await app.workers.wait_for_complete()
        await pilot.pause()

    result, source = drive(db, script)
    assert source.asked and "Nains" in source.asked[0]  # the search starts from the file's own name
    assert result["cells"][0][6] == "linked" and "Forge" in result["cells"][0][3]
    with Library(db) as lib:
        assert lib.attrs_of("file", lib.get_file(files["wrong"])["id"])["checked"].startswith("other:")


def test_the_next_batch_is_offered_when_the_batch_is_done(env):
    db, files = env

    async def script(app, pilot):
        await pilot.press("k")
        await pilot.press("k")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "AskScreen"
        await pilot.press("y")
        await pilot.pause()
        assert app.rows == [] and app.number == 2  # nothing left: the checked files are not shown again

    drive(db, script, unmatched=False)


def test_the_command_refuses_politely_without_a_terminal(env, capsys):
    from bdnex.ui import review_cmd

    assert review_cmd.run(["--batch", "10"]) == 2
    assert "run it in a terminal" in capsys.readouterr().err
