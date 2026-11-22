"""bdnex undo: the files a move run renamed go back."""

import io
import os

from bdnex.library import Library
from bdnex.config import load_config
from bdnex.ui import undo_cmd

from test_write_cmd import env, run  # noqa: F401  (the fixture and the runner of the write/move tests)


def undo(*argv):
    out = io.StringIO()
    code = undo_cmd.run(list(argv), out=out)
    return code, out.getvalue()


def test_nothing_to_list_before_any_move(env):
    code, text = undo()
    assert code == 0 and "No run has moved files yet" in text


def test_a_move_is_listed_and_undo_puts_the_file_back(env):
    tmp, files = env
    run("move", "--force", "volume:1")
    dest = tmp / "comics" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"
    assert dest.exists() and not os.path.exists(files["a"])
    code, text = undo()
    assert "1 file(s) moved" in text
    code, text = undo("last", "-n")
    assert "would move back" in text and dest.exists() and not os.path.exists(files["a"])  # a preview moves nothing
    code, text = undo("last")
    assert code == 0 and "1 file(s) moved back" in text
    assert os.path.exists(files["a"]) and not dest.exists()
    assert not (tmp / "comics" / "Series").exists()  # the folders the move had made are gone again
    with Library.for_settings(load_config().settings) as lib:
        assert lib.get_file(files["a"]) is not None and lib.get_file(str(dest)) is None
    assert "1 taken back" in undo()[1] and "nothing left" in undo()[1]
    assert "0 file(s) moved back" in undo("last")[1]  # nothing is moved twice


def test_a_copy_and_an_in_place_write_leave_nothing_to_undo(env):
    tmp, files = env
    run("move", "--force", "--copy", "volume:2")
    run("write", "--force")
    assert "No run has moved files yet" in undo()[1]


def test_undo_skips_what_changed_since(env):
    tmp, files = env
    run("move", "--force", "volume:1")
    dest = tmp / "comics" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"
    open(files["a"], "wb").write(b"another file took the old place")
    code, text = undo("last")
    assert code == 1 and "taken by another file" in text and dest.exists()
    os.remove(files["a"])
    os.remove(dest)
    code, text = undo("last")
    assert "is gone" in text


def test_an_unknown_run_is_refused(env):
    code, text = undo("nope")
    assert code == 1


def test_undo_does_not_move_a_file_the_library_no_longer_knows_or_into_a_place_it_already_holds(env):
    tmp, files = env
    run("move", "--force", "volume:1")
    dest = tmp / "comics" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"
    with Library.for_settings(load_config().settings) as lib:
        lib.remove_file(str(dest))  # forgotten since: moving it back would leave a file the library does not know
    code, text = undo("last")
    assert code == 1 and "no longer has" in text and dest.exists() and not os.path.exists(files["a"])
    with Library.for_settings(load_config().settings) as lib:
        lib.add_file(str(dest))
        lib.conn.execute("INSERT INTO files(path, md5, size, format) VALUES (?, 'stale', 1, 'cbz')", (lib._store(files["a"]),))  # a stale row
        lib.conn.commit()  # already holds the old path (the file itself is not there)
    code, text = undo("last")
    assert code == 1 and "already has a file at" in text and dest.exists()


def test_when_the_library_cannot_follow_the_file_goes_back_where_it_was(env, monkeypatch):
    tmp, files = env
    run("move", "--force", "volume:1")
    dest = tmp / "comics" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"

    def broken(self, old, new):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(Library, "move_file", broken)
    code, text = undo("last")
    assert code == 1 and "could not follow" in text
    assert dest.exists() and not os.path.exists(files["a"])  # disk and library agree: the file is where the library has it
    with Library.for_settings(load_config().settings) as lib:
        assert lib.get_file(str(dest)) is not None and not [b for b in lib.batches() if b["undone"]]
