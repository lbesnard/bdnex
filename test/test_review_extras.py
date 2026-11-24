"""`bdnex review --batch`: the folder tree, the duplicate screen, the form for many files, series or one-shot, the volume."""

import asyncio
import os
import shutil

import pytest
import yaml

pytest.importorskip("textual")

from bdnex.config import load_config  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402
from bdnex.library import review_batch as batch  # noqa: E402
from bdnex.ui import review_panels, review_tui  # noqa: E402

from test_review_tui import FakeSource, settle  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    root = tmp_path / "comics"
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db), "collection_path": str(root), "cover": {"check": False}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    paths = {}
    with Library(db, root=root) as lib:
        tintin = SeriesInfo("b", "t", "Tintin")
        for n in (1, 2, 3):
            album = lib.add_album(AlbumInfo("b", f"t{n}", f"Tintin {n}", series=tintin, volume=n, year=1950 + n))
            (root / "Series" / "Tintin").mkdir(parents=True, exist_ok=True)
            path = str(root / "Series" / "Tintin" / f"Tintin T{n}.cbz")
            shutil.copy(make_cbz(f"t{n}.cbz", pages=3), path)
            lib.add_file(path, album)
            lib.log_import(path, "tagged", 95.0, album)
            paths[f"t{n}"] = path
        klez = lib.add_album(AlbumInfo("b", "k5", "Kishinev-des-fous", series=SeriesInfo("b", "k", "Klezmer"), volume=5, year=2012))
        (root / "Series" / "Klezmer").mkdir(parents=True)
        for name, pages in (("main", 4), ("spare", 7)):
            path = str(root / "Series" / "Klezmer" / f"Klezmer T05 {name}.cbz")
            shutil.copy(make_cbz(f"k{name}.cbz", pages=pages), path)
            lib.add_file(path, klez)
            lib.log_import(path, "tagged", 90.0, klez)
            paths[name] = path
        lib.set_file_fields(paths["spare"], spare=1)
        lib.set_attr("file", lib.get_file(paths["t1"])["id"], batch.CHECKED, "keep")
    return db, root, paths


def drive(db, root, script, size=50):
    settings = load_config().settings
    out = {}

    async def go():
        with Library(db, root=root) as lib:
            app = review_tui.ReviewApp(lib, settings, FakeSource(), size, (), True, tree=True)
            async with app.run_test(size=(180, 50)) as pilot:
                await settle(app, pilot)
                await script(app, pilot, lib)
                await settle(app, pilot)
                out["app"] = app

    asyncio.run(go())
    return out


def test_the_tree_shows_how_far_each_folder_is_reviewed_and_a_click_opens_its_files(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        tree = app.folder_tree
        assert tree is not None
        seen["root"] = str(tree.root.label)
        series = tree.root.children[0]
        series.expand()
        await pilot.pause()
        seen["children"] = [(str(n.label), n.label.style) for n in series.children]
        seen["progress"] = app.sub_title
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        seen["rows"] = [os.path.basename(r.path) for r in app.rows]
        seen["previous"] = [r.previous for r in app.rows]
        await pilot.press("f")
        seen["hidden"] = app.query_one("#folderpane").display
        await pilot.press("f")
        seen["shown"] = app.query_one("#folderpane").display

    drive(db, root, script)
    assert seen["root"].startswith("◐ collection") and "1/5" in seen["root"]
    assert [text.split("  ")[0] for text, _ in seen["children"]] == ["○ Klezmer", "◐ Tintin"]
    assert [style for _, style in seen["children"]] == ["red", "dark_orange"]
    assert "reviewed 1/5" in seen["progress"]
    assert seen["rows"] == ["Tintin T1.cbz", "Tintin T2.cbz", "Tintin T3.cbz"]
    assert seen["previous"] == ["keep", "", ""]  # what was decided before is shown
    assert seen["hidden"] is False and seen["shown"] is True


def test_marking_a_folder_reviewed_turns_it_green(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("R")
        await pilot.press("y")
        await settle(app, pilot)
        seen["states"] = [r.status for r in app.rows]

    drive(db, root, script)
    with Library(db, root=root) as lib:
        assert all(lib.attrs_of("file", lib.get_file(paths[key])["id"]).get(batch.CHECKED) for key in ("t1", "t2", "t3"))
        assert not lib.attrs_of("file", lib.get_file(paths["main"])["id"]).get(batch.CHECKED)  # another folder: untouched
    assert seen["states"] == ["keep", "keep", "keep"]


def test_one_form_sets_the_common_fields_the_kind_and_a_volume_each_on_the_marked_files(env):
    db, root, paths = env

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("m")  # mark every file
        await pilot.press("E")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, review_panels.BulkScreen)
        assert screen.query_one("#b-publisher").placeholder == "(they differ)" or screen.query_one("#b-publisher").placeholder == ""
        screen.query_one("#b-series").value = "Les Aventures de Tintin"
        screen.query_one("#b-collection").value = "Hergé"
        screen.query_one("#b-category").value = "BD"
        screen.query_one("#bulkvolumes").text = "10\tTintin T1.cbz\n20\tTintin T2.cbz\n30\tTintin T3.cbz"
        screen.action_accept()
        await settle(app, pilot)

    drive(db, root, script)
    with Library(db, root=root) as lib:
        got = [lib.get_album(lib.get_file(paths[key])["album_id"]) for key in ("t1", "t2", "t3")]
        assert [(a["series"], a["collection"], a["category"], a["volume"]) for a in got] == [
            ("Les Aventures de Tintin", "Hergé", "BD", 10), ("Les Aventures de Tintin", "Hergé", "BD", 20),
            ("Les Aventures de Tintin", "Hergé", "BD", 30)]
        assert all(lib.attrs_of("file", lib.get_file(paths[k])["id"]).get(batch.CHECKED) for k in ("t1", "t2", "t3"))


def test_a_bad_value_in_the_form_writes_nothing(env):
    db, root, paths = env

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause()
        app.screen.query_one("#b-collection").value = "Mine"
        app.screen.query_one("#b-year").value = "twenty"
        app.screen.action_accept()
        await settle(app, pilot)

    drive(db, root, script)
    with Library(db, root=root) as lib:
        assert lib.get_album(lib.get_file(paths["t2"])["album_id"])["collection"] is None


def test_k_makes_a_series_album_a_one_shot_and_v_sets_a_volume(env):
    db, root, paths = env

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("K")  # the first row: a series album becomes a one-shot
        await settle(app, pilot)
        await pilot.press("down", "V")
        await pilot.pause()
        app.screen.query_one("Input").value = "42"
        await pilot.press("enter")
        await settle(app, pilot)

    drive(db, root, script)
    with Library(db, root=root) as lib:
        assert lib.get_album(lib.get_file(paths["t1"])["album_id"])["kind"] == "oneshot"
        assert lib.get_album(lib.get_file(paths["t2"])["album_id"])["volume"] == 42


def test_the_duplicate_screen_shows_both_files_and_can_trash_the_one_that_is_not_wanted(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Klezmer")
        await settle(app, pilot)
        await pilot.press("D")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, review_panels.DuplicateScreen)
        seen["files"] = [(t.path, t.main) for t in screen.found]
        seen["facts"] = str(screen.query_one("#dupfiles").render())
        await pilot.press("down")
        await pilot.press("t")  # the second file: the spare
        await settle(app, pilot)

    drive(db, root, script)
    assert seen["files"] == [(paths["main"], True), (paths["spare"], False)]
    assert "4 pages" in seen["facts"] and "7 pages" in seen["facts"]
    assert not os.path.exists(paths["spare"]) and os.path.exists(paths["main"])
    with Library(db, root=root) as lib:
        assert lib.get_file(paths["spare"]) is None and len(lib.trash_entries()) == 1


def test_a_different_version_keeps_its_file_and_gets_an_album_of_its_own(env):
    db, root, paths = env

    async def script(app, pilot, lib):
        app.open_folder("Series/Klezmer")
        await settle(app, pilot)
        await pilot.press("D")
        await pilot.pause()
        await pilot.press("down", "v")
        await pilot.pause()
        app.screen.query_one("#dupnote").value = "N&B"
        await pilot.press("enter")
        await settle(app, pilot)

    drive(db, root, script)
    assert os.path.exists(paths["spare"])
    with Library(db, root=root) as lib:
        new = lib.get_album(lib.get_file(paths["spare"])["album_id"])
        assert new["title"] == "Kishinev-des-fous (N&B)" and new["relation"] == "version" and new["source"] == "manual"


def test_a_folder_shows_the_files_that_have_no_album_and_no_import_record(env, make_cbz):
    db, root, paths = env
    with Library(db, root=root) as lib:
        stray = str(root / "Series" / "Tintin" / "Stray.cbz")
        shutil.copy(make_cbz("stray.cbz"), stray)
        lib.add_file(stray)
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        seen["rows"] = [(os.path.basename(r.path), r.kind) for r in app.rows]

    drive(db, root, script)
    assert ("Stray.cbz", "unmatched") in seen["rows"] and len(seen["rows"]) == 4
