"""`bdnex review --batch`: the folder tree, the duplicate screen, the form for many files, series or one-shot, the volume."""

import asyncio
import os
import shutil

import pytest
import yaml

pytest.importorskip("textual")

from bdnex.config import load_config  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402
from bdnex.library import manual  # noqa: E402
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


def drive(db, root, script, size=50, resume=None):
    settings = load_config().settings
    out = {}

    async def go():
        with Library(db, root=root) as lib:
            app = review_tui.ReviewApp(lib, settings, FakeSource(), size, (), True, tree=True, resume=resume)
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
    assert seen["root"].startswith("☐ ○ collection") and "1/5" in seen["root"]
    assert [text.split("  ")[0] for text, _ in seen["children"]] == ["☐ ○ Klezmer", "☐ ○ Tintin"]  # auto matched (strong scores), not all confirmed: cyan
    assert [style for _, style in seen["children"]] == ["cyan", "cyan"]
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
        seen["kinds"] = [(str(c[10]), str(c[11])) for c in [app.query_one("DataTable").get_row_at(i) for i in range(len(app.rows))]]

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
        groups["Review"].expand()
        groups["Other"].expand()
        await pilot.pause()
        seen["states"] = {str(n.label).split("  ")[0][2:]: str(n.label).split("  ")[1].split()[0] for n in [*groups["Review"].children, *groups["Other"].children]}
        app.open_facet("Other", "duplicates")
        await settle(app, pilot)
        seen["dups"] = sorted(os.path.basename(r.path) for r in app.rows)
        app.open_facet("Series", "Tintin")
        await settle(app, pilot)
        seen["tintin"] = len(app.rows)
        seen["choices"] = [c for c in app.facet_choices if c[0] == "Series"]

    drive(db, root, script)
    assert seen["groups"][:4] == ["Colour", "Link", "Review", "Other"] and "Series" in seen["groups"]
    assert seen["states"]["duplicates"] == "2" and seen["states"]["confirmed"] == "1"
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
        app.facet_tree.toggle([n for n in app.facet_tree.root.children if str(n.label).startswith("Review")][0].children[1])  # not confirmed
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


def test_a_click_on_a_triangle_opens_the_folder_and_a_filter_group(env):
    db, root, paths = env
    seen = {}

    async def opens(pilot, selector, get_node, offsets):
        """Click along the node's line until it is open (the arrow is a few cells in, depending on the depth); a click is given time to act.
        The node is looked up again each time: the trees are refreshed in the background."""
        for x in offsets:
            await pilot.click(selector, offset=(x, get_node().line))
            for _ in range(80):  # up to four seconds: a loaded machine is slow, and a second click would close it again
                await pilot.pause(0.05)
                if get_node().is_expanded:
                    return True
        return False

    async def script(app, pilot, lib):
        series = lambda: [n for n in app.folder_tree.root.children if "Series" in str(n.label)][0]  # noqa: E731
        assert not series().is_expanded
        seen["folder"] = await opens(pilot, "#foldertree", series, range(2, 9))
        seen["filter"] = await opens(pilot, "#facettree", lambda: app.facet_tree.root.children[1], range(0, 6))  # Kind

    drive(db, root, script)
    assert seen == {"folder": True, "filter": True}


def test_n_and_b_turn_the_pages_of_a_selection_fifty_at_a_time_by_default(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series")  # 5 files, 2 to a page
        await settle(app, pilot)
        seen["p1"] = [os.path.basename(r.path) for r in app.rows]
        seen["title1"] = app.sub_title
        await pilot.press("n")
        await settle(app, pilot)
        seen["p2"] = [os.path.basename(r.path) for r in app.rows]
        await pilot.press("n")
        await settle(app, pilot)
        seen["p3"] = [os.path.basename(r.path) for r in app.rows]
        await pilot.press("b")
        await settle(app, pilot)
        seen["back"] = [os.path.basename(r.path) for r in app.rows]
        await pilot.press("2")  # a sort keeps the page and the total
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)
        seen["after_sort"] = [os.path.basename(r.path) for r in app.rows]

    drive(db, root, script, size=2)
    assert len(seen["p1"]) == 2 and len(seen["p2"]) == 2 and len(seen["p3"]) == 1
    assert "1-2 of 5" in seen["title1"] and "page 1/3" in seen["title1"]
    assert seen["back"] == seen["p2"] and len(seen["after_sort"]) == 1  # sorted, and `n` still goes to the 3rd page
    assert not set(seen["p1"]) & set(seen["p2"]) and not set(seen["p2"]) & set(seen["p3"])  # no file twice


def test_a_line_of_tips_and_a_help_screen_say_how_to_use_it(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        seen["tip"] = str(app.query_one("#tips").render())
        app.next_tip()
        seen["next"] = str(app.query_one("#tips").render())
        await pilot.press("question_mark")
        await pilot.pause()
        seen["help"] = type(app.screen).__name__
        await pilot.press("escape")
        await pilot.pause()
        seen["closed"] = type(app.screen).__name__ != "HelpScreen"

    drive(db, root, script)
    assert seen["tip"].startswith("Tip: Double-click a column header") and seen["next"] != seen["tip"]
    assert seen["help"] == "HelpScreen" and seen["closed"]


def test_a_filter_group_you_opened_stays_open_when_the_counts_are_read_again(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        tree = app.facet_tree
        kind = [n for n in tree.root.children if str(n.label).startswith("Kind")][0]
        kind.expand()
        await pilot.pause()
        app.folders_ready(app.folder_root, app.folder_bad, {"Colour": [("red", 3)], "Kind": [("series", 5)]})  # what a refresh does
        await pilot.pause()
        again = [n for n in app.facet_tree.root.children if str(n.label).startswith("Kind")][0]
        seen["open"] = again.is_expanded
        seen["others"] = [n.is_expanded for n in app.facet_tree.root.children if not str(n.label).startswith("Kind")]

    drive(db, root, script)
    assert seen["open"] is True and seen["others"] == [True]  # (Colour starts open: it is the first thing to look at)


def test_the_form_says_several_values_when_the_albums_differ_and_shows_the_one_they_share_otherwise(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series")  # Tintin (3) and Klezmer (2): two series
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("e")  # several marked files: the same form as E
        await pilot.pause()
        seen["differ"] = (app.screen.query_one("#b-series").value, app.screen.query_one("#b-series").placeholder)
        app.screen.action_back()
        await pilot.pause()
        await pilot.press("x")
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause()
        seen["same"] = (app.screen.query_one("#b-series").value, app.screen.query_one("#b-series").placeholder)

    drive(db, root, script)
    assert seen["differ"] == ("", "(several values)") and seen["same"] == ("", "Tintin")


def test_hs_1_to_n_numbers_hors_series_and_v_takes_text_for_a_tome_with_no_number(env):
    db, root, paths = env

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause()
        app.screen.query_one("#hs").press()  # the button: Hors-série HS 1, HS 2...
        await pilot.pause()
        script.lines = app.screen.query_one("#bulkvolumes").text
        app.screen.action_accept()
        await settle(app, pilot)
        script.vols = [str(app.query_one("ReviewTable").get_row_at(i)[11]) for i in range(len(app.rows))]
        app.query_one("ReviewTable").move_cursor(row=0)
        await pilot.press("V")  # one file: a text is a number-less tome too
        await pilot.pause()
        app.screen.query_one("Input").value = "INT"
        await pilot.press("enter")
        await settle(app, pilot)

    drive(db, root, script)
    assert [line.split("\t")[0] for line in script.lines.split("\n")] == ["HS 1", "HS 2", "HS 3"] and script.vols == ["HS 1", "HS 2", "HS 3"]
    with Library(db, root=root) as lib:
        got = [lib.get_album(lib.get_file(paths[k])["album_id"]) for k in ("t1", "t2", "t3")]
        assert [(a["volume"], a["alternate_number"]) for a in got] == [(None, "INT"), (None, "HS 2"), (None, "HS 3")]


def test_shift_marks_a_list_of_rows_and_ticks_a_list_of_entries(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series")  # 5 files
        await settle(app, pilot)
        await pilot.click("ReviewTable", offset=(10, 1), control=True)  # row 0 marked (ctrl), the start of the list
        await pilot.click("ReviewTable", offset=(10, 4), shift=True)  # shift-click row 3: rows 0 to 3
        await pilot.pause()
        seen["marked"] = sorted(i for i, r in enumerate(app.rows) if id(r) in app.marked)
        await pilot.press("x")
        app.query_one("ReviewTable").move_cursor(row=0)
        await pilot.press("shift+down", "shift+down")  # the arrows with shift: 0, 1 and 2
        await pilot.pause()
        seen["arrows"] = sorted(i for i, r in enumerate(app.rows) if id(r) in app.marked)
        tree = app.folder_tree
        series = [n for n in tree.root.children if "Series" in str(n.label)][0]
        series.expand()
        await settle(app, pilot)
        lines = {str(n.label).split("  ")[0].split(" ")[-1]: n.line for n in series.children}
        await pilot.click("#foldertree", offset=(6, lines["Klezmer"]), control=True)
        await pilot.click("#foldertree", offset=(6, lines["Tintin"]), shift=True)
        await pilot.pause()
        seen["ticked"] = sorted(tree.ticked)

    drive(db, root, script)
    assert seen["marked"] == [0, 1, 2, 3] and seen["arrows"] == [0, 1, 2]
    assert seen["ticked"] == ["Series/Klezmer", "Series/Tintin"]


def test_the_help_chip_at_the_top_right_opens_the_help_and_the_colours_are_in_colour(env):
    from bdnex.ui import review_panels as panels

    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        await pilot.click("#helpchip")
        await pilot.pause()
        seen["help"] = type(app.screen).__name__

    drive(db, root, script)
    assert seen["help"] == "HelpScreen"
    text = panels.help_text()
    styles = {str(span.style) for span in text.spans}
    assert {"bold green", "bold cyan", "bold dark_orange", "bold red"} <= styles
    assert "SEARCH" in text.plain and "LINKING" in text.plain and "CONFIG" in text.plain and "WORDS" in text.plain
    assert "{" not in text.plain and "[" not in text.plain.replace("`[`", "")


def test_a_doubtful_file_you_link_is_no_longer_doubtful_after_the_fetch(env):
    db, root, paths = env
    with Library(db, root=root) as lib:
        lib.flag_review(paths["t2"], "looks like another volume")
        settings = load_config().settings
        row = next(r for r in batch.next_batch(lib, settings, size=50, everything=True) if r.path == paths["t2"])
        row.link, row.kind = "doubtful", "wrong"
        batch.link_info(lib, row, AlbumInfo("b", "t2", "Tintin 2", series=SeriesInfo("b", "t", "Tintin"), volume=2))
        assert row.link == "manual" and row.kind == "ok"  # at once, before any page
        lib.log_import(paths["t2"], "tagged", 97.0)  # what the fetch's import recorded
        batch.refresh_link(lib, row, 90.0)
        assert row.link == "auto matched" and row.score == 97.0
        lib.flag_review(paths["t2"], "doubt again")  # the page may find something against it: it is said, not hidden
        batch.refresh_link(lib, row, 90.0)
        assert row.link == "doubtful"


def test_narrow_the_view_adds_a_click_to_what_is_on_the_screen(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        from textual.widgets import Checkbox

        app.open_facet("Review", "confirmed")
        await settle(app, pilot)
        seen["alone"] = len(app.rows)  # box off: just the filter (all the confirmed files)
        app.query_one("#narrow", Checkbox).value = True
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        seen["narrowed"] = sorted(os.path.basename(r.path) for r in app.rows)  # the folder AND the filter kept from before
        app.open_facet("Link", "auto matched")
        await settle(app, pilot)
        seen["third"] = sorted(os.path.basename(r.path) for r in app.rows)  # the folder kept, the filters of two groups
        app.query_one("#narrow", Checkbox).value = False
        app.open_folder("Series/Klezmer")
        await settle(app, pilot)
        seen["off"] = len(app.rows)

    drive(db, root, script)
    assert seen["alone"] == 1 and seen["narrowed"] == ["Tintin T1.cbz"]
    assert seen["third"] == ["Tintin T1.cbz"] and seen["off"] == 2


def test_the_search_list_shows_the_author_and_year_the_library_already_has(env):
    db, root, paths = env
    with Library(db, root=root) as lib:
        album = lib.find_album("b", "t2")
        lib.edit_album(album, {"credits": {"Writer": ["Hergé"]}})
        assert batch.known_about(lib, "b", "t2") == ("Hergé", "1952")
        assert batch.known_about(lib, "b", "t", series=True)[1] == "1951"  # a series: its first year
        assert batch.known_about(lib, "b", "unknown") == ("", "")  # not fetched: nothing, no error


def test_arrows_under_the_table_turn_the_pages(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("")
        await settle(app, pilot)
        seen["first"] = [r.path for r in app.rows]
        seen["where"] = str(app.query_one("#pager-where").render())
        await pilot.click("#pager-next")
        await settle(app, pilot)
        seen["second"] = [r.path for r in app.rows]
        seen["where2"] = str(app.query_one("#pager-where").render())

    drive(db, root, script, size=2)
    assert seen["where"].startswith("page 1 of 3")
    assert seen["second"] and not set(seen["second"]) & set(seen["first"]) and seen["where2"].startswith("page 2 of 3")


def test_a_page_of_a_file_is_read_and_drawn_as_large_as_asked(env):
    from bdnex.importer import covers
    from bdnex.ui import cover_text

    db, root, paths = env
    image, total = covers.page_image(paths["t1"], "cbz", 1)
    assert image and total == 3
    assert covers.page_image(paths["t1"], "cbz", 99)[1] == 3  # past the end: the last page
    assert covers.page_image(str(root / "nothing.cbz"), "cbz") == (None, 0)
    text, by = cover_text.picture(image, 60, 30)
    assert text is not None and by in ("chafa", "half blocks") and len(text.plain.splitlines()) >= 2


def test_z_in_the_cover_popup_opens_the_full_size_page_and_arrows_turn_it(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("z")
        for _ in range(40):
            await pilot.pause(0.1)
            if type(app.screen).__name__ == "PageScreen" and app.screen.total:
                break
        seen["screen"] = type(app.screen).__name__
        seen["total"] = app.screen.total
        await pilot.press("right")
        await pilot.pause(0.3)
        seen["index"] = app.screen.index
        await pilot.press("escape")
        await pilot.pause()
        seen["back"] = type(app.screen).__name__

    drive(db, root, script)
    assert seen["screen"] == "PageScreen" and seen["total"] == 3 and seen["index"] == 1 and seen["back"] == "CoverScreen"


def big_page() -> bytes:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 800, 1200))
    pix.set_rect(pix.irect, (200, 120, 40))
    return pix.tobytes("png")


def test_zooming_a_page_shows_a_part_of_it_that_pans(env):
    from bdnex.importer import covers
    from bdnex.ui import cover_text

    db, root, paths = env
    image = big_page()
    whole = covers.image_size(image)
    same, fx, fy = cover_text.crop(image, 1, 0, 0, 100, 40)
    assert same == image and (fx, fy) == (0.0, 0.0)
    part, fx, fy = cover_text.crop(image, 2, 0, 0, 100, 40)
    size = covers.image_size(part)
    assert size[0] <= whole[0] // 2 + 1 and fx >= 0
    moved, _, _ = cover_text.crop(image, 2, 1, 1, 100, 40)  # the far corner: still a picture of the same size
    assert covers.image_size(moved) == size


def test_plus_and_the_arrows_zoom_and_move_over_the_page(env):
    import zipfile

    db, root, paths = env
    seen = {}
    with zipfile.ZipFile(paths["t1"], "w") as archive:  # a page with room to move over
        archive.writestr("p1.png", big_page())

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("z")
        for _ in range(40):
            await pilot.pause(0.1)
            if type(app.screen).__name__ == "PageScreen" and app.screen.total:
                break
        await pilot.press("plus")
        await pilot.pause(0.5)
        seen["zoom"] = app.screen.zoom
        await pilot.press("l")
        await pilot.pause(0.5)
        seen["px"] = app.screen.px
        await pilot.press("minus")
        await pilot.pause(0.3)
        seen["back"] = (app.screen.zoom, app.screen.px)

    drive(db, root, script)
    assert seen["zoom"] == 2 and seen["px"] > 0 and seen["back"] == (1, 0.0)


def test_the_picture_setting_draws_a_real_picture_over_the_page_and_falls_back_to_text(env, monkeypatch):
    from bdnex.ui import cover_text

    db, root, paths = env
    seen = {"writes": []}
    monkeypatch.setattr(cover_text, "graphic", lambda image, columns, rows, how: b"<PICTURE %s %dx%d>" % (how.encode(), columns, rows))
    monkeypatch.setattr(review_tui, "graphic", cover_text.graphic)

    async def script(app, pilot, lib):
        object.__setattr__(app.settings.cover, "picture", "kitty")
        monkeypatch.setattr(review_tui.PageScreen, "write_raw", lambda self, data: seen["writes"].append(data))
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("z")
        for _ in range(40):
            await pilot.pause(0.1)
            if any(b"<PICTURE kitty" in w for w in seen["writes"]):
                break
        await pilot.press("escape")
        await pilot.pause()

    drive(db, root, script)
    pictures = [w for w in seen["writes"] if b"<PICTURE kitty" in w]
    assert pictures and pictures[0].startswith(b"\x1b7\x1b[") and pictures[0].endswith(b"\x1b8")  # placed at the area, cursor put back
    assert any(w.startswith(b"\x1b_Ga=d") or b"_Ga=d" in w for w in seen["writes"])  # a kitty picture is deleted when the page goes away


def test_the_picture_option_is_in_the_config_with_chafa_as_the_default():
    settings = load_config().settings
    assert settings.cover.picture == "symbols"


def test_a_collection_the_site_gives_is_the_value_in_the_form_and_any_value_you_type_is_the_one_used(env):
    from bdnex.organizer import template
    from bdnex.ui import edit_cmd

    db, root, paths = env
    seen = {}
    with Library(db, root=root) as lib:
        album_id = lib.find_album("b", "t2")
        with lib.conn:
            lib.conn.execute("UPDATE albums SET extra = ? WHERE id = ?", ('{"edition_fields": {"Collection": "Collection Pilote"}}', album_id))
        assert manual.form_of(lib, album_id)["collection"] == "Collection Pilote"  # the value in use, not an empty box
        manual.update(lib, album_id, manual.form_of(lib, album_id))  # saved as it is: nothing is frozen
        assert lib.get_album(album_id)["collection"] is None
        assert template.fields_for(lib.get_album(album_id))["collection"] == "Collection Pilote"
        manual.update(lib, album_id, {**manual.form_of(lib, album_id), "collection": ""})  # emptied: none, not the site's again
        assert lib.get_album(album_id)["collection"] == "" and template.fields_for(lib.get_album(album_id))["collection"] == ""
        manual.update(lib, album_id, {**manual.form_of(lib, album_id), "collection": "Mine"})
        assert template.fields_for(lib.get_album(album_id))["collection"] == "Mine"
        # the same rules in `bdnex edit` and the bulk form (edit_cmd.doc_changes)
        manual.update(lib, album_id, {**manual.form_of(lib, album_id), "collection": ""})
        album = lib.get_album(album_id)
        assert edit_cmd.doc_changes(album, {"collection": None}, []) == {}  # already none
        assert edit_cmd.doc_changes(album, {"collection": "Collection Pilote"}, []) == {"collection": "Collection Pilote"}
        lib.edit_album(album_id, {"collection": None})  # back to the site's
        album = lib.get_album(album_id)
        assert edit_cmd.doc_changes(album, {"collection": "Collection Pilote"}, []) == {}
        assert edit_cmd.doc_changes(album, {"collection": None}, []) == {"collection": ""}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("down")  # Tintin T2
        await pilot.press("e")
        await pilot.pause(0.5)
        seen["value"] = app.screen.query_one("#f-collection").value

    drive(db, root, script)
    assert seen["value"] == "Collection Pilote"


def test_the_clear_button_clears_a_field_on_every_marked_file(env):
    db, root, paths = env
    seen = {}
    with Library(db, root=root) as lib:
        lib.edit_album(lib.find_album("b", "t1"), {"collection": "One"})
        lib.edit_album(lib.find_album("b", "t2"), {"collection": "Two"})

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        for key in ("m",):
            await pilot.press(key)
        await pilot.press("E")
        await pilot.pause(0.5)
        screen = app.screen
        seen["holder"] = screen.query_one("#b-collection").placeholder
        await pilot.click("#clear-collection")
        await pilot.pause(0.2)
        seen["after"] = screen.query_one("#b-collection").placeholder
        await pilot.click("#ok")
        await pilot.pause(0.8)
        seen["albums"] = [lib.get_album(lib.find_album("b", f"t{n}"))["collection"] for n in (1, 2, 3)]

    drive(db, root, script)
    assert seen["holder"] == "(several values)" and "cleared" in seen["after"]
    assert all(value in ("", None) for value in seen["albums"])


def test_choosing_series_in_the_form_fills_the_name_and_the_destination_follows(env, make_cbz):
    db, root, paths = env
    seen = {}
    (root / "One").mkdir()
    with Library(db, root=root) as lib:
        for n in (1, 2):
            album = lib.add_album(AlbumInfo("b", f"o{n}", f"Solo {n}"))
            path = str(root / "One" / f"Solo {n}.cbz")
            shutil.copy(make_cbz(f"o{n}.cbz", pages=3), path)
            lib.add_file(path, album)
            lib.log_import(path, "tagged", 95.0, album)

    async def script(app, pilot, lib):
        from textual.widgets import Select

        app.open_folder("One")
        await settle(app, pilot)
        seen["before"] = [r.dest for r in app.rows]
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause(0.5)
        screen = app.screen
        screen.query_one("#bulkkind", Select).value = "series"
        await pilot.pause(0.3)
        seen["name"] = screen.query_one("#b-series").value  # filled with the folder's name
        screen.query_one("#b-series").value = ""
        await pilot.click("#ok")
        await pilot.pause(0.3)
        seen["still_open"] = type(app.screen).__name__ == "BulkScreen"
        seen["problem"] = str(app.screen.query_one("#bulkproblem").render())
        app.screen.query_one("#b-series").value = "Solo Series"
        await pilot.click("#ok")
        await pilot.pause(0.8)
        seen["after"] = [r.dest for r in app.rows]

    drive(db, root, script)
    assert seen["name"] == "One" and seen["still_open"] and "name of the series" in seen["problem"]
    assert seen["before"] != seen["after"] and all(d.startswith("Series/Solo Series/") for d in seen["after"])


def test_the_kind_of_the_form_is_a_field_like_the_others(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        from textual.widgets import Select

        app.open_folder("Series/Tintin")  # three series albums
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause(0.5)
        seen["all_series"] = app.screen.query_one("#bulkkind", Select).value
        await pilot.click("#ok")  # nothing changed: saving is fine, nothing is written
        await pilot.pause(0.6)
        seen["closed"] = type(app.screen).__name__ != "BulkScreen"
        app.open_folder("Series")  # series albums only too: make one of them a one-shot, then they differ
        await settle(app, pilot)
        lib.make_oneshot(lib.find_album("b", "t3"))
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause(0.5)
        select = app.screen.query_one("#bulkkind", Select)
        seen["mixed"] = (select.value, "several" in select.classes)
        await pilot.press("escape")

    drive(db, root, script)
    assert seen["all_series"] == "series" and seen["closed"]
    assert seen["mixed"] == ("", True)


def test_m_marks_the_files_you_already_decided_too(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        for row in app.rows:  # all looked at (an edit, a confirm): they have a decision
            row.status = "keep"
        await pilot.press("m")
        seen["marked"] = len(app.marked)
        await pilot.press("m")
        seen["unmarked"] = len(app.marked)

    drive(db, root, script)
    assert seen["marked"] == 3 and seen["unmarked"] == 0


def test_save_is_always_in_view_in_the_popups_even_in_a_small_terminal(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        for name, keys in (("bulk", ("m", "E")), ("edit", ("e",))):
            if name == "bulk":
                await pilot.press("m")
            await pilot.press(*[k for k in keys if k != "m"])
            await pilot.pause(0.5)
            button = app.screen.query_one("#ok")
            seen[name] = (button.label.plain if hasattr(button.label, "plain") else str(button.label), button.region.bottom <= app.size.height and button.region.height > 0)
            await pilot.press("escape")
            await pilot.pause(0.3)

    settings = load_config().settings

    async def go():
        with Library(db, root=root) as lib:
            app = review_tui.ReviewApp(lib, settings, FakeSource(), 50, (), True, tree=True)
            async with app.run_test(size=(100, 22)) as pilot:  # a small terminal
                await settle(app, pilot)
                await script(app, pilot, lib)

    asyncio.run(go())
    assert seen["bulk"] == ("Save", True) and seen["edit"] == ("Save", True)


def test_a_page_has_its_would_go_to_at_once_without_waiting_for_the_plan_of_the_library(env):
    from bdnex.library import browse

    db, root, paths = env
    settings = load_config().settings
    with Library(db, root=root) as lib:
        rows = browse.rows_for(lib, settings, "folder", "Series/Tintin", planned=None)  # no plan yet: the first seconds of the screen
        assert len(rows) == 3 and all(r.dest.startswith("Series/Tintin/") for r in rows)
        only = browse.plan(lib, settings, files=[rows[0].file_id])
        assert list(only) == [rows[0].file_id] and only[rows[0].file_id] == rows[0].dest


def test_the_help_has_a_second_page_with_every_key(env):
    from bdnex.ui import review_panels as panels
    from bdnex.ui.review_tui import ReviewApp

    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        await pilot.press("question_mark")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        seen["page"] = app.screen.page
        await pilot.press("tab")
        await pilot.pause()
        seen["back"] = app.screen.page

    drive(db, root, script)
    assert seen == {"page": 1, "back": 0}
    plain = panels.keys_text().plain
    listed = {key.strip() for _, lines in panels.KEY_SECTIONS for keys, _ in lines for key in keys.replace("  ", " ").split(" ")}
    for binding in ReviewApp.BINDINGS:
        key = binding.key
        if key.isdigit() or key in ("question_mark", "shift+down", "shift+up", "ctrl+p"):
            continue
        assert key in listed or key == "space" and "space" in plain, f"key {key!r} is not on the keys page"


def test_the_form_for_one_file_has_a_clear_button_like_the_one_for_many(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        box = app.screen.query_one("#f-series")
        seen["before"] = box.value
        app.screen.query_one("#clear-series").press()
        await pilot.pause()
        seen["after"] = app.screen.query_one("#f-series").value

    drive(db, root, script)
    assert seen["before"] and seen["after"] == ""


def test_the_find_box_above_the_tree_keeps_only_the_folders_that_match(env):
    db, root, paths = env
    seen = {}

    def labels(tree):
        out = []

        def walk(node):
            for child in node.children:
                out.append(str(child.label))
                walk(child)

        walk(tree.root)
        return out

    async def script(app, pilot, lib):
        await settle(app, pilot)
        tree = app.folder_tree
        seen["all"] = len(labels(tree))
        await pilot.press("F")
        await pilot.pause()
        seen["focus"] = app.focused.id
        for ch in "tintin":
            await pilot.press(ch)
        await pilot.pause()
        seen["some"] = labels(tree)
        app.query_one("#treefind").value = "zzzzqq"
        await pilot.pause()
        seen["none"] = labels(tree)
        app.query_one("#treefind").value = ""
        await pilot.pause()
        seen["back"] = len(labels(tree))

    drive(db, root, script)
    assert seen["focus"] == "treefind"
    assert any("Tintin" in label for label in seen["some"])
    assert seen["none"] == []
    assert seen["back"] == seen["all"]


def test_the_buttons_of_the_form_say_what_they_do_when_the_mouse_is_over_them(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause()
        seen["tips"] = {b.id: b.tooltip for b in app.screen.query("Button")}

    drive(db, root, script)
    assert all(seen["tips"].values()), seen["tips"]
    assert "tome" in seen["tips"]["number"] and "hors-série" in seen["tips"]["hs"]


def test_every_button_and_clickable_word_of_the_screen_has_a_tooltip(env):
    from textual.widgets import Button, Checkbox, Input

    from bdnex.ui import review_panels as panels

    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        await settle(app, pilot)
        found = [w for w in app.query("*") if isinstance(w, (Button, Checkbox, panels.PagerChip, panels.HelpChip, panels.Splitter))
                 or getattr(w, "id", None) == "treefind"]
        seen["missing"] = [w.id for w in found if not w.tooltip]
        seen["count"] = len(found)
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("C")
        await pilot.pause()
        seen["columns"] = [b.id for b in app.screen.query(Button) if not b.tooltip]
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        seen["edit"] = [b.id for b in app.screen.query(Button) if not b.tooltip]

    drive(db, root, script)
    assert seen["count"] >= 8
    assert seen["missing"] == [] and seen["columns"] == [] and seen["edit"] == []


def test_the_find_box_above_the_filters_keeps_only_the_values_that_match(env):
    db, root, paths = env
    seen = {}

    def values(tree):
        return [str(n.label) for n in tree.all_nodes() if n.data and n.data[0] == "facet"]

    async def script(app, pilot, lib):
        await settle(app, pilot)
        tree = app.facet_tree
        seen["all"] = len(values(tree))
        seen["open0"] = tree.open_keys()
        await pilot.press("S")
        await pilot.pause()
        seen["focus"] = app.focused.id
        app.query_one("#facetfind").value = "tintin"
        await pilot.pause()
        seen["some"] = values(tree)
        app.query_one("#facetfind").value = "zzzzqq"
        await pilot.pause()
        seen["none"] = values(tree)
        app.query_one("#facetfind").value = ""
        await pilot.pause()
        seen["back"] = len(values(tree))
        seen["open1"] = tree.open_keys()

    drive(db, root, script)
    assert seen["focus"] == "facetfind"
    assert any("Tintin" in label for label in seen["some"]) and len(seen["some"]) <= seen["all"]
    assert seen["none"] == []
    assert seen["back"] == seen["all"]
    assert seen["open1"] == seen["open0"]


def test_a_restore_button_puts_a_field_back_to_what_the_site_says(env, monkeypatch):
    from bdnex.library import manual

    db, root, paths = env
    monkeypatch.setattr(manual, "site_form", lambda settings, library, album_id: {"publisher": "Dupuis (site)", "title": "Title (site)"})
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        seen["buttons"] = sorted(b.id for b in app.screen.query("Button") if (b.id or "").startswith("restore-"))
        app.screen.query_one("#f-publisher").value = "typed"
        app.screen.query_one("#restore-publisher").press()
        await pilot.pause()
        seen["value"] = app.screen.query_one("#f-publisher").value
        seen["tip"] = app.screen.query_one("#restore-publisher").tooltip
        album_id = app.current().album_id
        app.screen.action_accept()
        await settle(app, pilot)
        album = lib.get_album(album_id)
        seen["after"] = (album["publisher"], "publisher" in album["extra"].get("edits", {}))

    drive(db, root, script)
    assert "restore-publisher" in seen["buttons"] and "restore-series" not in seen["buttons"]
    assert seen["value"] == "Dupuis (site)" and "Dupuis (site)" in seen["tip"]
    assert seen["after"] == ("Dupuis (site)", False)  # saved, and your edit of it forgotten


def test_the_form_for_many_files_has_a_restore_button_that_asks_for_the_site_value_of_each(env, monkeypatch):
    from bdnex.library import manual

    db, root, paths = env
    monkeypatch.setattr(manual, "site_form", lambda settings, library, album_id: {"publisher": f"Site {album_id}"})
    monkeypatch.setattr(manual, "is_site_album", lambda library, album_id: True)
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause()
        screen = app.screen
        seen["has"] = bool(screen.query("#restore-publisher"))
        screen.query_one("#restore-publisher").press()
        await pilot.pause()
        seen["restored"] = set(screen.restored)
        screen.query_one("#restore-publisher").press()  # (taken back)
        await pilot.pause()
        seen["then"] = set(screen.restored)
        screen.query_one("#restore-publisher").press()
        await pilot.pause()
        screen.action_accept()
        await settle(app, pilot)
        ids = [r.album_id for r in app.rows]
        seen["publishers"] = [lib.get_album(i)["publisher"] for i in ids]
        seen["ids"] = ids

    drive(db, root, script)
    assert seen["has"] and seen["restored"] == {"publisher"} and seen["then"] == set()
    assert seen["publishers"] == [f"Site {i}" for i in seen["ids"]]


def test_the_cover_popup_follows_the_picture_setting_like_the_full_size_page(env, monkeypatch):
    from bdnex.ui import cover_text

    db, root, paths = env
    seen = {"writes": []}
    monkeypatch.setattr(cover_text, "graphic", lambda image, columns, rows, how: b"<PICTURE %s %dx%d>" % (how.encode(), columns, rows))
    monkeypatch.setattr(review_tui.Pictures, "write_raw", lambda self, data: seen["writes"].append(data))

    async def script(app, pilot, lib):
        object.__setattr__(app.settings.cover, "picture", "kitty")
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("c")
        for _ in range(40):
            await pilot.pause(0.1)
            if any(b"<PICTURE kitty" in w for w in seen["writes"]):
                break
        seen["screen"] = type(app.screen).__name__
        await pilot.press("escape")
        await pilot.pause()

    drive(db, root, script)
    assert seen["screen"] == "CoverScreen"
    assert any(b"<PICTURE kitty" in w and w.startswith(b"\x1b7\x1b[") for w in seen["writes"])
    assert any(b"_Ga=d" in w for w in seen["writes"])  # taken away when the popup closes


def test_chafa_is_run_without_the_terminal_so_its_questions_never_come_back_as_typed_keys(monkeypatch):
    """chafa asks the terminal what it can do; the answer arrives as keys (one is `c`, which closed the cover popup at once)."""
    import subprocess

    from bdnex.ui import cover_text

    calls = []

    def fake(cmd, **kw):
        calls.append(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"x", stderr=b"")

    monkeypatch.setattr(cover_text, "chafa_installed", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake)
    cover_text.picture(b"png", 20, 10)
    cover_text.graphic(b"png", 20, 10, "kitty")
    assert len(calls) == 2 and all(kw.get("start_new_session") for kw in calls)


def test_closing_a_real_picture_asks_tmux_to_redraw_unless_the_option_is_off(env, monkeypatch):
    import subprocess

    from bdnex.ui import cover_text

    db, root, paths = env
    asked = []
    monkeypatch.setattr(cover_text, "graphic", lambda image, columns, rows, how: b"<PICTURE>")
    cleared = []
    monkeypatch.setattr(review_tui.Pictures, "write_raw", lambda self, data: cleared.append(data) if data == b"\x1b[2J" else None)
    real = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, *a, **kw: asked.append(cmd) if cmd[0] == "tmux" else real(cmd, *a, **kw))
    monkeypatch.setenv("TMUX", "/tmp/tmux-1/default,1,0")

    async def script(app, pilot, lib):
        object.__setattr__(app.settings.cover, "picture", "kitty")
        object.__setattr__(app.settings.cover, "redraw_after_picture", True)
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("c")
        await pilot.pause(0.5)
        await pilot.press("escape")
        await pilot.pause(0.5)
        script.on = list(asked)
        asked.clear()
        object.__setattr__(app.settings.cover, "redraw_after_picture", False)
        await pilot.press("c")
        await pilot.pause(0.5)
        await pilot.press("escape")
        await pilot.pause(0.5)
        script.off = list(asked)
        object.__setattr__(app.settings.cover, "redraw_after_picture", True)
        object.__setattr__(app.settings.cover, "picture", "symbols")  # no real picture was drawn: nothing to clean up
        await pilot.press("c")
        await pilot.pause(0.5)
        await pilot.press("escape")
        await pilot.pause(0.5)
        script.text = list(asked)

    drive(db, root, script)
    assert script.on == [["tmux", "refresh-client"]] and script.off == [] and script.text == []
    assert len(cleared) == 1  # the terminal was cleared once (the first close), and the screen drawn again


def test_the_redraw_option_is_in_the_config_with_its_own_comment_and_off_by_default():
    from bdnex import config_template

    assert load_config().settings.cover.redraw_after_picture is False
    line = next(raw for raw in config_template.DEFAULTS_PATH.read_text(encoding="utf-8").splitlines() if "redraw_after_picture:" in raw)
    assert config_template._help_of(line) is not None  # a comment of its own, on the line (the user's file gets it with `bdnex config`)


def test_the_detail_pane_has_one_labelled_block_per_thing(env):
    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        row = app.rows[0]
        row.dest, row.album_url = "Series/Tintin/Tintin - T01 - Titre.cbz", "https://example.org/a.html"
        seen["text"] = app.detail_text(row).plain

    drive(db, root, script)
    lines = seen["text"].split("\n")
    heads = [line.split(" ")[0] for line in lines if line[:1].isalpha()]
    assert heads[:2] == ["FILE", "ALBUM"] and "GOES" in heads
    assert all(line.startswith(" " * 9) or line[:9].strip() for line in lines if line)  # a label, or the indent under it
    assert "Series/Tintin/" in seen["text"] and "Tintin - T01 - Titre.cbz" in seen["text"]


def test_the_detail_and_metadata_panes_scroll(env):
    from bdnex.ui.review_tui import Pane

    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        meta = app.query_one("#meta", Pane)
        meta.update("\n".join(f"line {n}" for n in range(200)))
        await pilot.pause()
        seen["scrolls"] = meta.max_scroll_y > 0 and isinstance(app.query_one("#detail"), Pane)

    drive(db, root, script)
    assert seen["scrolls"]


def test_a_popup_asked_twice_to_close_closes_once_and_the_screen_stays(env):
    """Enter held down (or a click and an Enter) sent two picks; the second dismiss raised ScreenStackError and the whole screen came down."""
    from bdnex.ui.review_tui import AskScreen

    db, root, paths = env
    seen = {"answers": []}

    async def script(app, pilot, lib):
        await settle(app, pilot)
        app.push_screen(AskScreen("Sure?", None), lambda yes: seen["answers"].append(yes))
        await pilot.pause()
        screen = app.screen
        screen.dismiss(True)
        screen.dismiss(True)  # the second one: nothing to close any more
        await pilot.pause()
        seen["screen"] = type(app.screen).__name__
        seen["alive"] = app.is_running

    drive(db, root, script)
    assert seen["answers"] == [True] and seen["alive"] and seen["screen"] != "AskScreen"


def test_the_form_for_many_files_makes_them_versions_with_a_name_from_the_dropdown(env):
    from textual.widgets import Select

    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.press("E")
        await pilot.pause()
        select = app.screen.query_one("#versionpick", Select)
        seen["choices"] = [value for _, value in select._options]
        seen["before"] = [r.album_id for r in app.rows]
        select.value = "Dossier"
        await pilot.pause()
        app.screen.action_accept()
        await settle(app, pilot)
        seen["albums"] = [lib.get_album(r.album_id) for r in app.rows]

    drive(db, root, script)
    assert seen["choices"][1:5] == ["Dossier", "Cahier", "Noir et blanc", "Croquis"]  # the first is "not a version", the last "another name"
    assert seen["albums"] and all(a["relation"] == "version" and a["title"].endswith("(Dossier)") for a in seen["albums"])
    assert all(a["id"] not in seen["before"] for a in seen["albums"])  # each file has an album of its own


def test_the_form_for_one_file_offers_the_versions_and_a_typed_name(env):
    from textual.widgets import Select

    db, root, paths = env
    seen = {}

    async def script(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        before = app.current().album_id
        app.screen.query_one("#versionpick", Select).value = "__other__"
        app.screen.query_one("#versionname").value = ""
        app.screen.action_accept()  # "another name" with no name typed: the form stays open and says so
        await pilot.pause()
        seen["open"] = type(app.screen).__name__
        seen["problem"] = str(app.screen.query_one("#problem").render())
        app.screen.query_one("#versionname").value = "Tirage de tête"
        app.screen.action_accept()
        await settle(app, pilot)
        album = lib.get_album(app.current().album_id)
        seen["album"] = (album["title"], album["relation"], app.current().album_id != before)

    drive(db, root, script)
    assert seen["open"] == "EditScreen" and "name of the version" in seen["problem"]
    assert seen["album"][0].endswith("(Tirage de tête)") and seen["album"][1:] == ("version", True)


def test_a_spare_version_can_go_inside_the_albums_own_folder():
    from pathlib import Path
    from types import SimpleNamespace

    from bdnex.organizer.organizer import Organizer

    def settings(place):
        return SimpleNamespace(duplicates=SimpleNamespace(versions_dir="_versions", versions_place=place))

    root = Path("/lib")
    top = Organizer._spare_path(SimpleNamespace(settings=settings("top")), root, ["Series", "Tintin"], "Tintin - T01.cbz")
    beside = Organizer._spare_path(SimpleNamespace(settings=settings("beside")), root, ["Series", "Tintin"], "Tintin - T01.cbz")
    assert top == "/lib/_versions/Series/Tintin/Tintin - T01.cbz"
    assert beside == "/lib/Series/Tintin/_versions/Tintin - T01.cbz"


def test_a_new_config_file_starts_with_the_collection_and_cycle_templates():
    import yaml

    from bdnex import config
    from bdnex.config_template import complete

    text, _ = complete(config.USER_TEMPLATE.format(version=config.CONFIG_VERSION, placeholder="/comics"))
    settings, _, _ = config.build_settings(yaml.safe_load(text))
    assert settings.paths.series.startswith("Series/%ifdef{collection,$collection}/$series%ifdef{cycle, - Cycle $cycle:02}/")
    assert settings.paths.oneshot == "OneShot/$title ($authors) [$year]/$title ($authors) [$year]"
    assert settings.duplicates.versions_place == "top" and settings.duplicates.version_labels[0] == "Dossier"


def test_continue_opens_the_screen_where_it_was_left(env):
    db, root, paths = env
    assert saved_place_of(db, root) is None  # nothing saved yet: `--continue` starts as usual
    seen = {}

    async def first(app, pilot, lib):
        app.open_folder("Series/Tintin")
        await settle(app, pilot)
        app.action_sort(1)
        app.action_sort(1)  # the file column, reversed
        app.query_one("#facetfind").value = "tintin"
        app.facet_tree.ticked.add(("Format", "cbz"))
        app.facet_tree.post_message(app.facet_tree.Ticked(app.facet_tree, 1))
        await pilot.pause(0.3)
        seen["first"] = [r.path for r in app.rows]

    drive(db, root, first)
    place = saved_place_of(db, root)
    assert place and place["picks"] == [["folder", "Series/Tintin"]] and place["sort"][1] is True and place["facetfind"] == "tintin"

    async def second(app, pilot, lib):
        for _ in range(30):  # the trees are built in the background
            if app.facet_tree is not None and app.facet_tree.ticked:
                break
            await pilot.pause(0.2)
        await settle(app, pilot)
        seen["again"] = [r.path for r in app.rows]
        seen["find"] = app.query_one("#facetfind").value
        seen["ticked"] = set(app.facet_tree.ticked)
        seen["title"] = app.sub_title

    drive(db, root, second, resume=place)
    assert seen["again"] == seen["first"] and len(seen["again"]) == 3  # the same files, in the same (reversed) order
    assert seen["find"] == "tintin" and seen["ticked"] == {("Format", "cbz")}


def saved_place_of(db, root):
    from bdnex.ui.review_cmd import saved_place

    with Library(db, root=root) as lib:
        return saved_place(lib)


def test_the_dpi_column_and_the_quality_filter_tell_how_sharp_the_files_are(env):
    db, root, paths = env
    seen = {}
    with Library(db, root=root) as lib:  # (measured before the screen opens: a soft scan, a sharp one, and three nobody measured)
        with lib.conn:
            lib.conn.execute("UPDATE files SET dpi = NULL")  # (the test pictures are tiny: registering them measured a few dpi)
        lib.set_dpi(paths["t1"], 90)
        lib.set_dpi(paths["t2"], 210)

    async def script(app, pilot, lib):
        app.view = [*app.view, "dpi"]
        app.open_selection("facet", ("Quality", "low"), "Quality: low")
        await settle(app, pilot)
        seen["low"] = [os.path.basename(r.path) for r in app.rows]
        row = app.rows[0]
        seen["cell"] = app.cells(1, row)[-1].plain
        from bdnex.library import browse

        seen["found"] = dict(browse.facets(lib)["Quality"])

    drive(db, root, script)
    assert seen["low"] == ["Tintin T1.cbz"] and seen["cell"] == "~90"
    assert seen["found"]["low"] == 1 and seen["found"]["good"] == 1 and seen["found"]["not measured"] == 3
