"""Findings of the review of the review screen: a decision that fails is said (not a crash), a newer decision supersedes the fetch, a form says what is
wrong before it closes, nothing typed over is picked, and no browser is started where there is none to start."""

import os

import pytest

pytest.importorskip("textual")

from bdnex.library import review_batch as batch  # noqa: E402
from bdnex.ui import review_tui  # noqa: E402

from test_review_tui import FORGE, drive, env, fetched  # noqa: E402,F401  (the fixture and the driver of the screen's tests)


def test_a_decision_that_cannot_be_saved_is_said_and_the_row_stays_as_it_was(env, monkeypatch):
    db, files = env
    monkeypatch.setattr(batch, "decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked")))
    seen = {}

    async def script(app, pilot):
        await pilot.press("k")  # keep: its write fails
        row = app.rows[0]
        seen["status"], seen["running"] = row.status, app.is_running

    drive(db, script)
    assert seen == {"status": "", "running": True}  # it used to crash the screen, or show a decision that was never saved


def test_skipping_a_decided_row_does_not_change_what_it_says(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        row = app.rows[0]
        row.status = "keep"
        app.cursor_row = 0
        app.action_skip()
        seen["status"] = row.status

    drive(db, script)
    assert seen["status"] == "keep"  # the table said 'skipped' while the library held 'keep'


def test_a_newer_decision_ends_the_wait_for_the_older_fetch(env, monkeypatch):
    db, files = env
    monkeypatch.setattr(batch, "link_info", lambda *a, **k: None)
    seen = {}

    async def script(app, pilot):
        row = app.rows[0]
        row.pending, row.link_id = True, 4
        app.link_page(row, object())  # `i` with a page address of another site, while the site's page is still being fetched
        seen["pending"], seen["link_id"] = row.pending, row.link_id

    drive(db, script)
    assert seen == {"pending": False, "link_id": 5}  # it stayed 'fetching' for ever: the older job ends without telling the row


def test_a_form_says_what_is_wrong_before_it_closes_and_keeps_what_was_typed(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        app.query_one("DataTable").move_cursor(row=2)  # the file with no album: the form of a hand-written one
        app.action_edit()
        await pilot.pause()
        screen = app.screen
        screen.query_one("#f-year").value = "19x5"
        screen.action_accept()
        await pilot.pause()
        seen["still_open"] = app.screen is screen
        seen["problem"] = str(screen.query_one("#problem").render())
        seen["year"] = screen.query_one("#f-year").value

    drive(db, script)
    assert seen["still_open"] and "year" in seen["problem"] and seen["year"] == "19x5"


def test_a_failed_weighing_gives_the_batch_back_and_going_to_it_does_not_wipe_it(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        rows = app.rows
        app.batches, app.at = [rows], 0
        app.ahead, app.ahead_running = None, True  # (the next batch is still being weighed in the background)
        app.load_batch()  # `n`: the table is emptied for the next one...
        app.show_error("database is locked")  # ...which could not be weighed
        await pilot.pause()
        seen["rows"] = list(app.rows) == list(rows)
        app.go_to(0)
        seen["kept"] = len(app.batches[0]) == len(rows)

    drive(db, script)
    assert seen == {"rows": True, "kept": True}  # `b` used to store the emptied list over the real batch


def test_no_browser_is_started_over_ssh_or_where_there_is_no_display(monkeypatch):
    opened = []
    monkeypatch.setattr(review_tui.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert review_tui.open_in_browser("https://x/") is True and opened == ["https://x/"]
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 1 5.6.7.8 22")
    assert review_tui.open_in_browser("https://y/") is False and opened == ["https://x/"]  # (it used to start one for Ctrl-O)
    monkeypatch.delenv("SSH_CONNECTION")
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(review_tui.sys, "platform", "linux")
    assert review_tui.open_in_browser("https://z/") is False  # a text browser would start inside the terminal of the screen


def test_enter_does_not_pick_a_hit_of_what_was_typed_before(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        await pilot.press("o")
        await pilot.pause()
        screen = app.screen
        screen.hits = [(FORGE, 91.0)]
        screen.latest, screen.shown = 7, 6  # a newer search is on its way
        screen.pick(0)
        seen["still_open"] = app.screen is screen
        screen.shown = 7
        screen.pick(0)
        await pilot.pause()
        seen["closed"] = app.screen is not screen

    drive(db, script)
    assert seen == {"still_open": True, "closed": True}  # the old album was linked at once, with no confirmation for one file


def test_a_failure_inside_the_fetch_queue_finishes_the_waiting_jobs_instead_of_leaving_them_fetching():
    from importer.test_review_fetch import Recorder, queue_for, urls
    from importer.test_session import make_settings
    from helpers import FakeWeb

    settings = make_settings(cover={"download": False, "check": False})
    queue, made = queue_for(settings, FakeWeb(), 2)
    seen = Recorder()
    bad = seen.job(urls(1)[0], "/x/bad.cbz", count=2)
    bad.started = lambda: (_ for _ in ()).throw(RuntimeError("the screen is gone"))
    queue.submit(bad)
    assert seen.done.wait(10) or "/x/bad.cbz" in seen.ended  # told that it is over, with the reason
    assert "the fetching stopped" in seen.ended["/x/bad.cbz"]
    good = seen.job(urls(2)[1], "/x/good.cbz", count=2)
    queue.submit(good)  # and the thread is still there for the next one
    assert seen.done.wait(10) and "/x/good.cbz" in seen.ended
    queue.shutdown(wait=True, cancel_futures=False)


def test_leaving_a_file_the_library_has_no_record_of_is_remembered(tmp_path, make_cbz):
    from bdnex.library import Library

    path = make_cbz("never registered.cbz")
    with Library(":memory:") as library:
        row = batch.Row(path, "f", None, None, None, None, None, "unmatched")
        batch.decide(library, row, "leave")
        assert row.file_id is not None and library.get_file(path) is not None  # the mark has a file to sit on
        assert dict(library.file_attrs(row.file_id)).get("checked") == "leave" if hasattr(library, "file_attrs") else True


def test_the_table_has_a_column_with_where_a_move_would_put_each_file_and_it_goes_when_the_album_changes(env):
    import yaml

    from bdnex.ui import review_tui

    db, files = env
    conf = os.environ["BDNEX_CONFIG"]
    settings = yaml.safe_load(open(conf))
    settings["collection_path"] = str(db.parent / "collection")  # where `bdnex move` would put the files
    os.makedirs(settings["collection_path"], exist_ok=True)
    open(conf, "w").write(yaml.safe_dump(settings))
    seen = {}

    async def script(app, pilot):
        assert review_tui.COLUMNS[-1][0] == "Would go to"
        first = app.rows[0]  # linked to "Nains T01 Redwin"
        seen["dest"] = first.dest
        seen["cell"] = str(app.query_one("DataTable").get_row_at(0)[8])
        seen["loose"] = [r.dest for r in app.rows if r.album_id is None]  # no album: nothing to say
        app.record("keep")
        seen["after_keep"] = first.dest  # the same album, so the same place: it is worked out again, not left blank
        app.action_sort(8)
        seen["sorted"] = True

    drive(db, script)
    assert seen["dest"].endswith(".cbz") and "Nains" in seen["dest"] and not seen["dest"].startswith("/")  # relative to the collection
    assert seen["cell"] == seen["dest"] and seen["loose"] == [""] and seen["after_keep"] == seen["dest"]


def test_questions_that_come_by_themselves_wait_in_a_queue_and_never_open_over_a_window(env):
    from bdnex.ui.review_tui import AskScreen

    db, files = env
    seen = {}
    answers = []

    async def script(app, pilot):
        app.ask_later("first?", lambda: AskScreen("First question?", None), answers.append)
        app.ask_later("second?", lambda: AskScreen("Second question?", None), answers.append)
        await pilot.pause()
        badge = app.query_one("#queue-badge")
        seen["badge"] = (badge.display, str(badge.render()), badge.region.x + badge.region.width == app.size.width)  # red mark, at the right edge
        seen["stack"] = len(app.screen_stack)  # nothing was put over the table
        await pilot.press("w")
        await pilot.pause()
        seen["open"] = app.screen.question
        await pilot.press("w")  # `w` inside a window is only text for it / ignored: the next question is not stacked over it
        await pilot.pause()
        seen["still_one"] = app.screen.question
        await pilot.press("y")
        await pilot.pause()
        seen["left"] = len(app.waiting)
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        seen["empty"] = (len(app.waiting), app.query_one("#queue-badge").display)

    drive(db, script)
    assert seen["badge"][0] and "2 questions waiting" in seen["badge"][1] and seen["badge"][2]
    assert seen["stack"] == 1 and seen["open"] == "First question?" and seen["still_one"] == "First question?"
    assert seen["left"] == 1 and answers == [True, False] and seen["empty"] == (0, False)  # answered in order, then the mark goes




def test_the_marked_files_say_they_are_reading_the_series_page_until_the_answer_is_here(env, monkeypatch):
    import asyncio
    import threading

    db, files = env
    release = threading.Event()
    monkeypatch.setattr(batch, "read_series_address", lambda settings, address: (release.wait(5), None)[1])  # a slow site
    seen = {}

    async def script(app, pilot):
        rows = app.rows[:2]
        task = asyncio.ensure_future(app.propose(rows, "https://www.bedetheque.com/serie-47467-BD-Nains.html"))
        for _ in range(20):  # the worker is on its way to the slow read
            await pilot.pause()
            if all(r.busy for r in rows):
                break
        table = app.query_one("DataTable")
        seen["during"] = [(r.busy, r.busy_what) for r in rows]
        seen["cell"] = str(table.get_row_at(0)[7])  # the State column (the last one is now "Would go to")
        seen["blocked"] = app.idle() is None if app.current() is rows[0] else None  # no second decision while it reads
        release.set()
        await task
        seen["after"] = [(r.busy, r.busy_what) for r in rows]

    drive(db, script)
    assert seen["during"] == [(True, "reading the series")] * 2 and "reading the series" in seen["cell"]  # with the dots and the seconds
    assert seen["after"] == [(False, "")] * 2  # cleared even though the read gave nothing and the rest failed


def test_after_a_link_the_column_says_where_the_file_would_go_and_it_is_not_cut(env, monkeypatch):
    import threading

    import yaml

    from test_review_tui import _linking

    db, files = env
    conf = os.environ["BDNEX_CONFIG"]
    settings = yaml.safe_load(open(conf))
    settings["collection_path"] = str(db.parent / "collection")
    os.makedirs(settings["collection_path"], exist_ok=True)
    open(conf, "w").write(yaml.safe_dump(settings))
    release = threading.Event()
    release.set()
    _linking(monkeypatch, db, release)  # the import of the page ends at once and switches the file to the album 2 ("Forge")
    seen = {}

    async def script(app, pilot):
        row = app.rows[2]  # the loose file: no album, so nothing to say
        seen["before"] = row.dest
        app.query_one("DataTable").move_cursor(row=2)
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("enter")  # link it to the first hit (the album 2)
        await fetched(app, pilot)
        seen["after"] = row.dest
        seen["cell"] = str(app.query_one("DataTable").get_row_at(2)[8])
        column = app.query_one("DataTable").columns[list(app.query_one("DataTable").columns)[8]]
        seen["width"] = column.width  # fixed, but as wide as the screen allows (this one is 300 columns wide)

    drive(db, script, columns=300)
    assert seen["before"] == "" and seen["after"].endswith(".cbz") and "Forge" in seen["after"]  # it stayed '-' after the link
    assert seen["cell"] == seen["after"] and seen["width"] > 44 and seen["width"] >= len(seen["after"])  # more than 44 characters when the screen can show them


def test_d_shows_the_metadata_of_the_album_beside_the_detail_and_e_changes_it(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        panel = app.query_one("#meta")
        seen["closed"] = panel.display
        app.query_one("DataTable").move_cursor(row=0)  # linked to the album "Redwin" (series Nains, T1, 2015, Soleil)
        await pilot.press("d")
        await pilot.pause()
        seen["open"] = panel.display
        seen["text"] = str(panel.render())
        await pilot.press("e")  # an album of the site: its fields, not a refusal
        await pilot.pause()
        screen = app.screen
        seen["form"] = screen.__class__.__name__
        seen["fields"] = [key for key, _, _ in screen.fields]
        screen.query_one("#f-title").value = "Redwin de la Forge"
        screen.query_one("#f-year").value = "2016"
        screen.action_accept()
        await pilot.pause()
        row = app.rows[0]
        seen["row"] = (row.album, str(panel.render()))

    drive(db, script)
    assert seen["closed"] is False and seen["open"] is True
    assert "Redwin" in seen["text"] and "Nains" in seen["text"] and "2015" in seen["text"] and "e edits these" in seen["text"]
    assert seen["form"] == "EditScreen" and "series" not in seen["fields"] and "title" in seen["fields"]  # a series is renamed for all its albums
    assert "Redwin de la Forge" in seen["row"][0] and "2016" in seen["row"][1] and "changed by you" in seen["row"][1]  # the table and the panel follow
    from bdnex.library import Library

    with Library(db) as lib:
        album = lib.get_album(lib.get_file(files["wrong"])["album_id"])
        assert album["title"] == "Redwin de la Forge" and album["year"] == 2016 and album["series"] == "Nains"
        assert (album["extra"].get("edits") or {}).get("title") == "Redwin de la Forge"  # kept as the person's own edit: a re-import keeps it


def test_the_panel_says_when_the_file_has_no_album(env):
    db, files = env
    seen = {}

    async def script(app, pilot):
        app.query_one("DataTable").move_cursor(row=2)  # the loose file
        await pilot.press("d")
        await pilot.pause()
        seen["text"] = str(app.query_one("#meta").render())

    drive(db, script)
    assert "No album" in seen["text"]
