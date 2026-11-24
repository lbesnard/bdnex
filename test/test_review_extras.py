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
    assert seen["root"].startswith("☐ ◐ collection") and "1/5" in seen["root"]
    assert [text.split("  ")[0] for text, _ in seen["children"]] == ["☐ ○ Klezmer", "☐ ◐ Tintin"]  # all matched, none reviewed: yellow, not red
    assert [style for _, style in seen["children"]] == ["yellow", "dark_orange"]
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


def test_a_folder_with_subfolders_shows_every_file_below_it(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        series = app.folder_tree.root.children[0]
        series.expand()
        await pilot.pause()
        app.folder_tree.select_node(series)  # a click on `Series`, which only has subfolders
        await settle(app, pilot)
        seen["rows"] = sorted(os.path.basename(r.path) for r in app.rows)
        seen["kinds"] = [(str(c[9]), str(c[10])) for c in [app.query_one("DataTable").get_row_at(i) for i in range(len(app.rows))]]

    drive(db, root, script)
    assert len(seen["rows"]) == 5 and "Klezmer T05 main.cbz" in seen["rows"] and "Tintin T3.cbz" in seen["rows"]
    assert ("series", "5") in seen["kinds"] and ("series", "2") in seen["kinds"]  # the Kind and Vol columns, on the right


def test_the_filters_list_albums_by_state_series_genre_and_a_click_shows_their_files(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        tree = app.facet_tree
        assert isinstance(tree, review_panels.FacetTree)
        groups = {str(n.label).split("  ")[0]: n for n in tree.root.children}
        seen["groups"] = list(groups)
        groups["State"].expand()
        await pilot.pause()
        seen["states"] = {str(n.label).split("  ")[0][2:]: str(n.label).split("  ")[1].split()[0] for n in groups["State"].children}
        app.open_facet("State", "duplicates")
        await settle(app, pilot)
        seen["dups"] = sorted(os.path.basename(r.path) for r in app.rows)
        app.open_facet("Series", "Tintin")
        await settle(app, pilot)
        seen["tintin"] = len(app.rows)
        seen["choices"] = [c for c in app.facet_choices if c[0] == "Series"]

    drive(db, root, script)
    assert seen["groups"][:2] == ["State", "Kind"] and "Series" in seen["groups"]
    assert seen["states"]["unreviewed"] == "4" and seen["states"]["duplicates"] == "2" and seen["states"]["reviewed"] == "1"
    assert seen["dups"] == ["Klezmer T05 main.cbz", "Klezmer T05 spare.cbz"] and seen["tintin"] == 3
    assert ("Series", "Klezmer", 2) in seen["choices"]


def test_the_second_tree_shows_where_a_move_would_put_the_files_and_follows_an_edit(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        for _ in range(20):  # the plan is worked out in the background
            if app.move_tree is not None:
                break
            await pilot.pause(0.2)
        seen["tops"] = [str(n.label).split("  ")[0].split(" ")[-1] for n in app.move_tree.root.children]
        app.open_selection("planned", "Series", "organised: Series")
        await settle(app, pilot)
        seen["dests"] = sorted(r.dest for r in app.rows)
        # an edit: the album of the first Tintin file goes to another series; its place in the second tree follows
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause()
        app.screen.query_one("#b-series").value = "Hergé"
        app.screen.action_accept()
        await settle(app, pilot)
        for _ in range(30):
            if any("Hergé" in dest for dest in app.planned.values()):
                break
            await pilot.pause(0.2)
        seen["after"] = sorted(d for d in app.planned.values() if "Hergé" in d)

    drive(db, root, script)
    assert seen["tops"] == ["_versions", "Series"]  # the spare version goes to its own folder
    assert len(seen["dests"]) == 4 and all(d.startswith("Series/") for d in seen["dests"])
    assert len(seen["after"]) == 3 and seen["after"][0].startswith("Series/Hergé/Hergé - T01")


def test_the_command_palette_has_the_actions_and_the_filter_values(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        titles = [c.title for c in app.get_system_commands(app.screen)]
        seen["titles"] = titles
        from bdnex.ui.review_panels import FacetCommands

        provider = FacetCommands(app.screen)
        hits = [hit async for hit in provider.search("series klez")]
        seen["hits"] = [h.help for h in hits]

    drive(db, root, script)
    assert "Duplicates: the other files of this album" in seen["titles"] and "Edit the marked files" in seen["titles"]
    assert "Series or one-shot" in seen["titles"] and "Folders: show or hide the left panel" in seen["titles"]
    assert seen["hits"] == ["2 file(s)"]


def test_ticking_folders_and_a_filter_shows_the_files_of_all_of_them_together(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        tree = app.folder_tree
        series = tree.root.children[0]
        series.expand()
        await pilot.pause()
        names = {str(n.label).split("  ")[0][2:].split(" ", 1)[1]: n for n in series.children}
        tree.toggle(names["Klezmer"])  # space does this on the cursor's node
        seen["boxes"] = str(names["Klezmer"].label)[:1]
        app.facet_tree.toggle([n for n in app.facet_tree.root.children if str(n.label).startswith("State")][0].children[1])  # unreviewed
        await pilot.pause()
        seen["button"] = str(app.query_one("#show-ticked").label)
        app.action_show_ticked()
        await settle(app, pilot)
        seen["rows"] = sorted(os.path.basename(r.path) for r in app.rows)
        app.action_clear_ticks()
        await pilot.pause()
        seen["cleared"] = (str(app.query_one("#show-ticked").label), tree.ticked == set())

    drive(db, root, script)
    assert seen["boxes"] == "☑" and seen["button"] == "Show ticked (2)"
    assert seen["rows"] == ["Klezmer T05 main.cbz", "Klezmer T05 spare.cbz"]  # the folder AND not reviewed yet
    assert seen["cleared"] == ("Show ticked", True)


def test_a_letter_shows_every_folder_under_it_and_ctrl_click_marks_a_row(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        from bdnex.library import folders

        for n in range(folders.MANY + 3):  # many folders: the tree groups them by first letter
            (root / "OneShot" / f"Zed {n}").mkdir(parents=True, exist_ok=True)
        # a folder with many children is grouped: build it through the library module and the panel directly
        counts = {f"Big/A{n}": [1, 0, 0] for n in range(folders.MANY + 3)} | {"Big/B1": [1, 0, 0]}
        big = folders.build(counts)
        groups = folders.find(big, "Big").shown_children()
        seen["groups"] = [(g.name, len(g.children)) for g in groups]
        # a click on a letter opens all its folders at once
        app.open_selections([("folder", f"Series/{name}") for name in ("Tintin", "Klezmer")], "two folders")
        await settle(app, pilot)
        seen["rows"] = len(app.rows)
        await pilot.click("ReviewTable", offset=(10, 2), control=True)  # the second row, with ctrl
        await pilot.pause()
        seen["marked"] = len(app.marked)

    drive(db, root, script)
    assert seen["groups"] == [("A", 83), ("B", 1)] and seen["rows"] == 5 and seen["marked"] == 1


def test_a_double_click_on_a_header_changes_that_column_s_field_and_the_view_is_saved(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.click("ReviewTable", offset=(10, 0), times=2)  # the header of the 2nd column (File)
        await pilot.pause()
        seen["screen"] = type(app.screen).__name__
        app.screen.dismiss("format")
        await settle(app, pilot)
        seen["titles"] = [str(c.label) for c in app.query_one("ReviewTable").columns.values()][:4]
        app.set_view(["file", "series", "vol", "size"])
        await settle(app, pilot)
        seen["cells"] = [str(c) for c in app.query_one("ReviewTable").get_row_at(0)]

    drive(db, root, script)
    assert seen["screen"] == "ColumnChoiceScreen" and seen["titles"][:2] == ["#", "Format"]
    assert seen["cells"][1:3] == ["Tintin", "1"] and seen["cells"][0] == "Tintin T1.cbz"
    with Library(db, root=root) as lib:  # what was left is what the next run opens with
        import json

        assert json.loads(lib.get_meta("review.view"))["columns"] == ["file", "series", "vol", "size"]


def test_the_panes_can_be_resized_with_the_mouse_and_the_sizes_and_tab_are_remembered(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        seen["before"] = app.query_one("#folderpane").size.width
        splitter = app.query_one("#split-left")
        await pilot.mouse_down(splitter)
        await pilot.hover(None, offset=(60, 10))  # drag to the right
        await pilot.mouse_up(None, offset=(60, 10))
        await settle(app, pilot)
        seen["after"] = app.query_one("#folderpane").size.width
        app.query_one("#split-detail").drag_to(80, 30)  # (the same, called directly: the height of the detail pane)
        app.query_one("#split-detail").post_message(review_panels.Splitter.Moved(app.query_one("#split-detail")))
        app.query_one("#trees").active = "tab-moves"
        await settle(app, pilot)
        seen["detail"] = app.query_one("#detailrow").size.height

    drive(db, root, script)
    assert seen["after"] > seen["before"] + 10
    with Library(db, root=root) as lib:
        import json

        saved = json.loads(lib.get_meta("review.view"))
        assert saved["sizes"]["folderpane"] == seen["after"] and saved["tab"] == "tab-moves"
        assert saved["sizes"]["detailrow"] == seen["detail"]
