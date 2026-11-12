"""Duplicates at import: the comparison, the three answers, spare versions and the trash."""

import os
import shutil

import pytest

from bdnex.importer.session import DUPLICATE, TAGGED, NeedsTerminal
from bdnex.library.hashing import file_md5
from bdnex.ui.prompts import InquirerPrompter
from bdnex.versions import KEEP_EXISTING, KEEP_NEW
from helpers import ALBUM_URL, FakeWeb

from .test_session import FILE, Env, Bedetheque, make_settings


class Chooser:
    """A prompter that records the comparison it is shown and answers as told."""

    def __init__(self, answer):
        self.answer, self.shown = answer, []

    def choose_match(self, path, matches):
        return matches[0].candidate if matches else None

    def resolve_duplicate(self, comparison, what, root=None):
        self.shown.append((comparison, what, root))
        return self.answer


def env_with(answer, **settings):
    env = Env(FakeWeb(), make_settings(**settings), Chooser(answer))
    env.source.update_sitemaps()
    env.source.album_info = lambda url, recache=False: Bedetheque.album_info(env.source, ALBUM_URL, recache)
    return env


def two_files(make_cbz, env, pages=(3, 5)):
    first = env.session.import_file(make_cbz(f"1 {FILE}", pages=pages[0]))
    second_path = make_cbz(f"2 {FILE}", pages=pages[1])
    return first, second_path


def test_the_prompter_is_shown_both_files_and_a_suggestion(make_cbz):
    env = env_with("skip")
    first, second = two_files(make_cbz, env, pages=(3, 9))
    env.session.import_file(second)
    (comparison, what, root), = env.session.prompter.shown
    assert what == "album already linked" and root == env.lib.root
    assert comparison.new.pages == 9 and comparison.existing[0].pages == 3
    assert comparison.suggestion == KEEP_NEW and "9 pages against 3" in comparison.reason


def test_keeping_the_existing_file_leaves_the_new_one_unimported(make_cbz):
    env = env_with("skip")
    first, second = two_files(make_cbz, env, pages=(5, 3))
    result = env.session.import_file(second)
    assert result.outcome == DUPLICATE and env.lib.get_file(second) is None and os.path.exists(second)
    assert env.session.prompter.shown[0][0].suggestion == KEEP_EXISTING


def test_keeping_the_new_file_sends_the_old_one_to_the_trash(make_cbz):
    env = env_with("replace")
    first, second = two_files(make_cbz, env)
    old_path = env.lib.files_for_album(first.album_id)[0]["path"]
    result = env.session.import_file(second)
    assert result.outcome == TAGGED and env.lib.get_file(old_path) is None and not os.path.exists(old_path)
    (entry,) = env.lib.trash_entries()
    assert entry["path"] == old_path and entry["album_id"] == first.album_id and os.path.exists(entry["trash_path"])
    assert env.lib.get_file(second)["album_id"] == first.album_id and env.lib.get_file(second)["spare"] == 0
    assert "trash" in result.note


def test_keeping_both_makes_the_new_file_a_spare_version(make_cbz):
    env = env_with("keep_both")
    first, second = two_files(make_cbz, env)
    result = env.session.import_file(second)
    files = {os.path.basename(f["path"]): f for f in env.lib.files_for_album(first.album_id)}
    assert len(files) == 2 and files[f"2 {FILE}"]["spare"] == 1 and files[f"1 {FILE}"]["spare"] == 0
    assert "spare version" in result.note


def test_a_third_version_is_a_spare_too(make_cbz):
    env = env_with("keep_both")
    two_files(make_cbz, env)
    env.session.import_file(make_cbz(f"2 {FILE}", pages=5))
    env.session.import_file(make_cbz(f"3 {FILE}", pages=7))
    album = env.lib.find_album("bedetheque", "245127")
    assert sorted(f["spare"] for f in env.lib.files_for_album(album)) == [0, 1, 1]


def test_an_identical_file_can_be_skipped_or_replace_the_old_one(make_cbz):
    for answer in ("skip", "replace"):
        env = env_with(answer)
        original = make_cbz(f"orig {FILE}", pages=4)
        copy = original.replace("orig", "copy")
        shutil.copy(original, copy)
        env.session.import_file(original)
        result = env.session.import_file(copy)
        assert file_md5(copy) == file_md5(original) if os.path.exists(original) else True
        assert env.session.prompter.shown[-1][1] == "identical file"
        if answer == "skip":
            assert result.outcome == DUPLICATE and env.lib.get_file(copy) is None
        else:
            assert result.outcome == TAGGED and env.lib.get_file(original) is None and len(env.lib.trash_entries()) == 1
            assert env.lib.get_file(copy) is not None


def test_the_configured_action_decides_without_asking(make_cbz):
    env = env_with("skip", duplicates={"action": "keep_both"})
    first, second = two_files(make_cbz, env)
    env.session.import_file(second)
    assert env.session.prompter.shown == [] and env.lib.get_file(second)["spare"] == 1


def test_a_preview_and_auto_all_never_ask(make_cbz):
    for kw in ({"pretend": True}, {"auto": "all"}):
        env = Env(FakeWeb(), make_settings(), Chooser("replace"), **kw)
        env.source.update_sitemaps()
        env.source.album_info = lambda url, recache=False, e=env: Bedetheque.album_info(e.source, ALBUM_URL, recache)
        if "pretend" in kw:
            env.session.pretend = False
        a = env.session.import_file(make_cbz(f"1 {FILE}", pages=3))
        env.session.pretend = kw.get("pretend", False)
        env.session.import_file(make_cbz(f"2 {FILE}", pages=5))
        assert env.session.prompter.shown == []


def test_the_real_prompter_needs_a_terminal_for_this_question(make_cbz, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    env = env_with(None)
    env.session.prompter = InquirerPrompter()
    first, second = two_files(make_cbz, env)
    with pytest.raises(NeedsTerminal):
        env.session.import_file(second)
