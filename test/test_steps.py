"""``bdnex init``, the import steps (--match / --fetch), skip memory, --force as "redo", the progress bar."""

import io

import pytest
import yaml

from bdnex.importer.bedetheque import Bedetheque
from bdnex.importer.session import ALREADY, PARTIAL, PRETEND, SKIPPED, TAGGED
from bdnex.library import Library
from bdnex.ui import import_cmd, init_cmd
from bdnex.formatting import human_duration
from bdnex.ui.output import Bar
from helpers import ALBUM_URL, FakeWeb
from importer.test_session import FILE, Env, FakePrompter, make_settings


# ------------------------------------------------------------------ progress bar
class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_bar_on_a_terminal_redraws_one_line_with_percent_and_eta():
    out, clock = io.StringIO(), Clock()
    bar = Bar(4, "Pages", stream=out, tty=True, clock=clock)
    for name in ("a", "b"):
        clock.now += 10
        bar.update(name)
    text = out.getvalue()
    assert text.count("\r") == 2 and "50%" in text and "2/4" in text and "about 20 s left" in text and text.endswith("b")
    bar.finish("done")
    assert out.getvalue().endswith("\r\x1b[Kdone\n")  # the line is cleared and replaced by the message


def test_bar_off_a_terminal_prints_plain_lines_every_ten_percent():
    out = io.StringIO()
    bar = Bar(100, "List", stream=out, tty=False)
    for _ in range(100):
        bar.update("x")
    lines = out.getvalue().splitlines()
    assert 8 <= len(lines) <= 12 and "\r" not in out.getvalue() and lines[-1].startswith("List [") and "100%" in lines[-1]


def test_an_estimated_total_never_shows_100_percent_and_grows_when_low():
    out = io.StringIO()
    bar = Bar(2, "List", stream=out, tty=False, exact=False)
    bar.update()
    bar.update()
    assert "100%" not in out.getvalue()
    bar.update()
    assert bar.total == 4 and bar.percent() < 100


@pytest.mark.parametrize("seconds, text", [(5, "5 s"), (89, "89 s"), (120, "2 min"), (7200, "2.0 h")])
def test_human_duration(seconds, text):
    assert human_duration(seconds) == text


# ------------------------------------------------------------------------- init
@pytest.fixture
def cli(tmp_path, monkeypatch, make_cbz):
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(tmp_path / "lib.sqlite"),
                                    "import": {"auto": True}, "bedetheque": {"delay": [0, 0]}}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    web = FakeWeb()
    import bdnex.importer.bedetheque as plugin
    monkeypatch.setattr(plugin, "http_fetch", web)
    monkeypatch.setattr(plugin, "build_fetcher", lambda settings: web)
    original = Bedetheque.album_info
    monkeypatch.setattr(Bedetheque, "album_info", lambda self, url, recache=False: original(self, ALBUM_URL, recache))
    monkeypatch.setattr("bdnex.importer.bedetheque.bedetheque.uniform", lambda a, b: 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return web, tmp_path / "lib.sqlite", make_cbz


def init(*argv):
    out, bars = io.StringIO(), io.StringIO()
    return init_cmd.run(list(argv), out=out, bar_stream=bars), out.getvalue(), bars.getvalue()


def run_import(*argv):
    out = io.StringIO()
    return import_cmd.run(list(argv), out=out), out.getvalue()


def test_init_downloads_the_list_with_a_progress_bar(cli):
    web, db, _ = cli
    code, text, bars = init()
    assert code == 0 and "Done:" in text and "albums in the list" in text and "bdnex import" in text
    assert "Album list (bedetheque) [" in bars and "%" in bars
    with Library(db) as lib:
        assert lib.get_meta("sitemaps_at") and int(lib.get_meta("sitemap_albums")) > 0
        assert lib.cache_summary()["sitemap"][0] >= 26


def test_running_init_again_refreshes_every_file_and_reports_new_albums(cli):
    web, db, _ = cli
    init()
    first = sum(1 for url in web.calls if url.endswith("_map.xml"))
    web.calls.clear()
    code, text, _ = init()
    assert code == 0 and sum(1 for url in web.calls if url.endswith("_map.xml")) >= first  # all of them again
    assert "since the last time" in text


def test_init_says_when_the_list_cannot_be_downloaded(cli, monkeypatch, capsys):
    web, _, _ = cli
    import bdnex.importer.bedetheque as plugin

    def down(url):
        raise OSError("network is down")

    monkeypatch.setattr(plugin, "http_fetch", down)
    monkeypatch.setattr(plugin, "build_fetcher", lambda settings: down)
    assert init()[0] == 1 and "network is down" in capsys.readouterr().err


# ----------------------------------------------------------- import without a list
def test_import_without_an_album_list_stops_and_names_the_command(cli, capsys):
    _, _, make_cbz = cli
    assert run_import(make_cbz(FILE))[0] == 1
    assert "bdnex init" in capsys.readouterr().err


def test_on_a_terminal_import_offers_to_download_the_list(cli, monkeypatch, capsys):
    web, db, make_cbz = cli
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)
    asked = []
    monkeypatch.setattr("bdnex.ui.prompts.InquirerPrompter.confirm",
                        lambda self, message, default=False: asked.append((message, default)) or False)
    assert run_import(make_cbz(FILE))[0] == 1 and asked and asked[0][1] is True and "70 MB" in asked[0][0]
    assert not any(url.endswith("_map.xml") for url in web.calls)  # declined: nothing downloaded
    monkeypatch.setattr("bdnex.ui.prompts.InquirerPrompter.confirm", lambda self, message, default=False: True)
    code, text = run_import(make_cbz(FILE))
    assert code == 0 and "1 file(s): 1 complete" in text and any(url.endswith("_map.xml") for url in web.calls)


# ------------------------------------------------------------------------ steps
def test_without_step_flags_both_steps_run(cli):
    _, db, make_cbz = cli
    init()
    code, text = run_import(make_cbz(FILE))
    assert "1 complete" in text


def test_match_alone_requests_no_page_and_fetch_alone_completes_it(cli):
    web, db, make_cbz = cli
    init()
    path = make_cbz(FILE)
    web.calls.clear()
    code, text = run_import("--match", path)
    assert code == 0 and "1 matched" in text and ALBUM_URL not in web.calls
    assert "bdnex import --fetch" in text
    code, text = run_import("--fetch", path)
    assert "1 complete" in text and ALBUM_URL in web.calls


def test_fetch_alone_without_a_path_takes_everything_in_the_library(cli):
    web, db, make_cbz = cli
    init()
    run_import("--match", make_cbz(FILE))
    code, text = run_import("--fetch")
    assert code == 0 and "1 complete" in text


def test_match_needs_a_path(cli, capsys):
    assert run_import("--match")[0] == 2 and "Give the files or folders" in capsys.readouterr().err


def test_fetch_on_a_file_that_is_not_matched_says_so(cli):
    _, _, make_cbz = cli
    init()
    code, text = run_import("--fetch", make_cbz(FILE))
    assert "not matched yet" in text and "1 skipped" in text


def test_old_option_names_still_work_and_say_what_replaced_them(cli, capsys):
    web, db, make_cbz = cli
    init()
    path = make_cbz(FILE)
    code, text = run_import("--no-fetch", path)
    assert "1 matched" in text and "--no-fetch is now --match" in capsys.readouterr().err
    code, text = run_import("--retry-partial", path)
    assert "1 complete" in text and "--retry-partial is now --fetch" in capsys.readouterr().err
    code, text = run_import("--recache", path)
    assert "--recache is now --fetch --force" in capsys.readouterr().err


def test_already_imported_files_are_counted_and_not_listed_unless_verbose(cli):
    _, _, make_cbz = cli
    init()
    path = make_cbz(FILE)
    run_import(path)
    code, text = run_import(path)
    assert "1 file(s): 1 already" in text and path not in text and "`--force` redoes them" in text
    code, text = run_import("-v", path)
    assert path in text and "already imported as Nains - T01" in text


# ------------------------------------------------------- the session: force, skips
def prepared(web, settings=None, prompter=None, **kw):
    env = Env(web, settings or make_settings(), prompter, **kw)
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    return env


class Picker:
    """Chooses the n-th candidate offered."""

    def __init__(self, index=0):
        self.index, self.asked = index, []

    def choose_match(self, path, matches):
        self.asked.append(path)
        return matches[self.index].candidate if matches else None

    def duplicate_action(self, path, others, what):
        return "skip"


def test_a_wrong_match_is_redone_with_match_and_force(make_cbz):
    web = FakeWeb()
    env = prepared(web, make_settings(**{"import": {"auto": False}}), Picker(0), steps=("match",))
    path = make_cbz(FILE)
    first = env.session.import_file(path)
    assert env.session.import_file(path).outcome == ALREADY and len(env.session.prompter.asked) == 1
    env.session.prompter.index = 1
    env.session.force = True
    again = env.session.import_file(path)
    assert len(env.session.prompter.asked) == 2 and again.album_id != first.album_id
    assert env.lib.get_file(path)["album_id"] == again.album_id


def test_force_with_the_fetch_step_reads_the_page_again(make_cbz):
    web = FakeWeb()
    env = prepared(web, steps=("match", "fetch"))
    path = make_cbz(FILE)
    assert env.session.import_file(path).outcome == TAGGED
    assert env.session.import_file(path).outcome == ALREADY
    env.session.steps, env.session.force = frozenset(["fetch"]), True
    seen = []
    env.source.album_info = lambda url, recache=False: seen.append(recache) or Bedetheque.album_info(
        env.source, ALBUM_URL, recache)
    assert env.session.import_file(path).outcome == TAGGED and seen == [True]  # asked the site, not the cache


def test_a_skipped_file_is_not_asked_about_again(make_cbz):
    env = prepared(FakeWeb(), make_settings(**{"import": {"auto": False}}), Picker())
    env.session.prompter.choose_match = lambda path, matches: env.session.prompter.asked.append(path)  # skip
    path = make_cbz("Nains Redwin.cbz")
    assert env.session.import_file(path).outcome == SKIPPED
    result = env.session.import_file(path)
    assert result.outcome == SKIPPED and "skipped earlier" in result.note and len(env.session.prompter.asked) == 1


def test_force_looks_at_a_skipped_file_again(make_cbz):
    env = prepared(FakeWeb(), make_settings(**{"import": {"auto": False}}), Picker())
    env.session.prompter.choose_match = lambda path, matches: env.session.prompter.asked.append(path)
    path = make_cbz("Nains Redwin.cbz")
    env.session.import_file(path)
    env.session.force = True
    env.session.import_file(path)
    assert len(env.session.prompter.asked) == 2


def test_new_albums_from_init_give_skipped_files_another_chance(make_cbz):
    env = prepared(FakeWeb(), make_settings(**{"import": {"auto": False}}), Picker())
    env.session.prompter.choose_match = lambda path, matches: env.session.prompter.asked.append(path)
    path = make_cbz("Nains Redwin.cbz")
    env.session.import_file(path)
    env.lib.set_meta("sitemaps_at", "2999-01-01 00:00:00")  # `bdnex init` found new albums since
    env.session.import_file(path)
    assert len(env.session.prompter.asked) == 2


def test_skip_memory_does_not_hide_files_from_a_match_free_run(make_cbz):
    env = prepared(FakeWeb(), make_settings(**{"import": {"auto": False}}), Picker(), steps=("fetch",))
    assert "not matched yet" in env.session.import_file(make_cbz(FILE)).note


def test_auto_all_never_asks_and_applies_medium_matches(make_cbz):
    env = prepared(FakeWeb(), make_settings(match={"strong_threshold": 100}), FakePrompter(), auto="all")
    assert env.session.import_file(make_cbz("Nains Redwin.cbz")).outcome == TAGGED
    assert env.session.prompter.asked == []


def test_meta_values(make_cbz):
    lib = Library(":memory:")
    assert lib.get_meta("x") is None
    lib.set_meta("x", "1")
    lib.set_meta("x", "2")
    assert lib.get_meta("x") == "2"
    lib.set_meta("now")
    assert len(lib.get_meta("now")) == 19  # YYYY-MM-DD HH:MM:SS, the format of the import log


# ------------------------------------------------ nobody to ask (no terminal)
def test_a_question_without_a_terminal_stops_the_import_once_with_an_explanation(cli, capsys):
    web, db, make_cbz = cli
    init()
    # a name that is not a strong match: it has to be asked about (auto is off in this run)
    path = make_cbz("Nains Redwin.cbz")
    code, text = run_import("--no-auto", path)
    err = capsys.readouterr().err
    assert code == 2 and "no terminal to ask on" in err and "--auto" in err and "Traceback" not in err
    with Library(db) as lib:
        assert lib.query([], files=True) == []  # nothing half-recorded


def test_confident_matches_need_no_terminal_with_auto(cli):
    _, _, make_cbz = cli
    init()
    code, text = run_import("--auto", make_cbz(FILE))
    assert code == 0 and "1 complete" in text


def test_a_bare_auto_does_not_swallow_the_path_after_it(cli):
    _, _, make_cbz = cli
    init()
    path = make_cbz(FILE)
    args = import_cmd.build_parser().parse_args(["--auto=strong", path])
    assert args.auto == "strong" and args.paths == [path]
    code, text = run_import("--auto", path)  # the form the documentation shows
    assert code == 0 and "1 complete" in text


def test_auto_all_is_the_only_form_with_a_value(cli):
    assert import_cmd.build_parser().parse_args(["--auto=all", "x"]).auto == "all"
    with pytest.raises(SystemExit):
        import_cmd.build_parser().parse_args(["--auto=maybe", "x"])


def test_a_preview_does_not_ask_about_duplicates(cli, make_cbz):
    _, _, make_cbz = cli
    init()
    first = make_cbz(FILE)
    copy = first.replace(".cbz", " copy.cbz")
    import shutil
    shutil.copy(first, copy)
    run_import("--auto", first)
    code, text = run_import("--auto", "-n", copy)  # identical bytes: it would have asked what to do
    assert code == 0 and "duplicate" in text


def test_series_flags():
    parse = import_cmd.build_parser().parse_args
    assert parse(["x"]).series is None  # the configuration decides
    assert parse(["--series", "x"]).series == "always" and parse(["--no-series", "x"]).series == "never"
    with pytest.raises(SystemExit):
        parse(["--series", "--no-series", "x"])
