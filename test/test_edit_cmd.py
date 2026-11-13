"""``bdnex edit``, ``bdnex relocate`` and ``bdnex ls --unmatched / --missing``."""

import io
import os

import pytest
import yaml

from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.ui import edit_cmd, ls_cmd, relocate_cmd


class Yes:
    def __init__(self, answer=True):
        self.answer, self.asked = answer, []

    def confirm(self, message):
        self.asked.append(message)
        return self.answer


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with Library(db) as lib:
        series = SeriesInfo("b", "s1", "Nains")
        one = lib.add_album(AlbumInfo("b", "1", "Redwin", series=series, volume=1, publisher="Soleil", year=2007,
                                      summary="Line one\nLine two", credits={"Writer": ["Jarry, Nicolas"]},
                                      genres=["Fantasy"]))
        two = lib.add_album(AlbumInfo("b", "2", "Ulrog", series=series, volume=2))
        shot = lib.add_album(AlbumInfo("b", "3", "Bibliomule", year=2021))
    return db, (one, two, shot)


def run(argv, editor=None, prompter=None):
    out = io.StringIO()
    code = edit_cmd.run(argv, out=out, editor=editor or (lambda text: text), prompter=prompter or Yes())
    return code, out.getvalue()


def album(db, album_id):
    with Library(db) as lib:
        return lib.get_album(album_id)


def edit_text(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


# ------------------------------------------------------------------- the editor
def test_untouched_text_changes_nothing(env):
    code, text = run(["series:nains"])
    assert code == 0 and "Nothing changed" in text


def test_editing_values_in_the_editor(env):
    db, (one, two, _) = env
    code, text = run(["series:nains", "volume:1"],
                     editor=lambda t: edit_text(edit_text(t, "title: Redwin", "title: Redwin de la Forge"),
                                                "Writer: ['Jarry, Nicolas']", "Writer: ['Jarry, Nicolas', Co]"))
    assert code == 0 and "title: 'Redwin' -> 'Redwin de la Forge'" in text and "1 album(s) updated" in text
    got = album(db, one)
    assert got["title"] == "Redwin de la Forge" and got["credits"]["Writer"] == ["Co", "Jarry, Nicolas"]
    assert album(db, two)["title"] == "Ulrog"  # not selected


def test_multiline_summary_round_trips_unchanged(env):
    code, text = run(["series:nains", "volume:1"], editor=lambda t: (t if "Line one" in t else pytest.fail(t)))
    assert code == 0 and "Nothing changed" in text


def test_deleted_lines_keep_values_but_null_clears(env):
    db, (one, _, _) = env
    lines = lambda t: [l for l in t.splitlines() if not l.startswith(("  year:", "  summary:", "    "))]
    run(["series:nains", "volume:1"], editor=lambda t: "\n".join(
        ["- id: %d" % one, "  year: null", "  publisher: ''"]))
    got = album(db, one)
    assert got["year"] is None and got["publisher"] is None
    assert got["title"] == "Redwin" and got["credits"] == {"Writer": ["Jarry, Nicolas"]}  # absent = kept


def test_renaming_the_series_applies_to_all_its_albums(env):
    db, (one, two, _) = env
    code, text = run(["series:nains"], editor=lambda t: edit_text(t, "series: Nains", "series: Les Nains"))
    assert "renames the series for all its albums" in text
    assert album(db, one)["series"] == album(db, two)["series"] == "Les Nains"


def test_oneshot_has_no_series_line_and_refuses_one(env, capsys):
    db, (_, _, shot) = env
    seen = {}
    run(["bibliomule"], editor=lambda t: seen.setdefault("t", t))
    assert "\n  series:" not in seen["t"]
    answers = iter([lambda t: t.replace("year: 2021", "year: 2021\n  series: X"), lambda t: t])
    code, text = run(["bibliomule"], editor=lambda t: next(answers)(t))
    assert code == 1 and "Cancelled" in text and album(db, shot)["kind"] == "oneshot"


def test_mistakes_reopen_the_editor_with_the_reasons(env):
    db, (one, _, _) = env
    seen = []

    def editor(text):
        seen.append(text)
        if len(seen) == 1:
            return edit_text(text, "year: 2007", "year: soon")
        return edit_text(text.split("#\n", 1)[1] if text.startswith("# ---- fix") else text, "year: soon", "year: 2008")

    code, text = run(["series:nains", "volume:1"], editor=editor)
    assert code == 0 and seen[1].startswith("# ---- fix these") and "year must be a whole number" in seen[1]
    assert album(db, one)["year"] == 2008


def test_leaving_the_text_unchanged_after_a_mistake_cancels(env):
    db, (one, _, _) = env
    calls = []

    def editor(text):
        calls.append(text)
        return text.replace("year: 2007", "year: soon") if len(calls) == 1 else text

    code, text = run(["series:nains", "volume:1"], editor=editor)
    assert code == 1 and "Cancelled" in text and album(db, one)["year"] == 2007


@pytest.mark.parametrize("edit, reason", [
    (lambda t: t.replace("volume: 1", "volume: 0.5"), "volume must be a whole number"),
    (lambda t: t.replace("month: null", "month: 13"), "month must be a whole number between 1 and 12"),
    (lambda t: t.replace("id: 1", "id: 99"), "unknown id 99"),
    (lambda t: t + "\n- id: 1\n  title: x\n", "listed twice"),
    (lambda t: t.replace("title: Redwin", "titel: Redwin"), "unknown field(s) titel"),
    (lambda t: t.replace("genres: [Fantasy]", "genres: {a: b}"), "genres: expected a list"),
    (lambda t: "just text", "must stay a list"),
    (lambda t: "- id: [", "not valid YAML"),
])
def test_invalid_edits_are_explained(env, edit, reason):
    seen = []

    def editor(text):
        seen.append(text)
        return edit(text) if len(seen) == 1 else text

    code, _ = run(["series:nains", "volume:1"], editor=editor)
    assert code == 1 and reason in seen[1]


def test_declining_the_confirmation_changes_nothing(env):
    db, (one, _, _) = env
    prompter = Yes(False)
    code, text = run(["series:nains", "volume:1"], editor=lambda t: edit_text(t, "year: 2007", "year: 1999"), prompter=prompter)
    assert code == 1 and prompter.asked and album(db, one)["year"] == 2007


def test_yes_skips_the_confirmation_and_pretend_stores_nothing(env):
    db, (one, _, _) = env
    prompter = Yes(False)
    change = lambda t: edit_text(t, "year: 2007", "year: 1999")
    run(["-n", "series:nains", "volume:1"], editor=change, prompter=prompter)
    assert album(db, one)["year"] == 2007 and not prompter.asked
    run(["-y", "series:nains", "volume:1"], editor=change, prompter=prompter)
    assert album(db, one)["year"] == 1999 and not prompter.asked


# ----------------------------------------------------------------------- --set
def test_set_changes_every_matching_album_without_an_editor(env):
    db, (one, two, _) = env
    code, text = run(["series:nains", "--set", "publisher=Delcourt", "--set", "genres=Humour; Fantasy",
                      "--set", "credits.Writer=A, B; C", "--set", "year=2010", "-y"],
                     editor=lambda t: pytest.fail("the editor must not open"))
    assert code == 0
    for album_id in (one, two):
        got = album(db, album_id)
        assert got["publisher"] == "Delcourt" and got["year"] == 2010
        assert got["genres"] == ["Fantasy", "Humour"] and got["credits"]["Writer"] == ["A, B", "C"]


@pytest.mark.parametrize("item", ["publisher", "colour=red", "year=soon", "=x"])
def test_bad_set_is_refused(env, item, capsys):
    code, _ = run(["series:nains", "--set", item, "-y"])
    assert code == 2 and "bad --set" in capsys.readouterr().err


def test_set_series_skips_oneshots(env, capsys):
    db, (_, _, shot) = env
    code, _ = run(["year:2021", "--set", "series=X", "-y"])
    assert "no series" in capsys.readouterr().err and album(db, shot)["kind"] == "oneshot"


def test_no_matching_album_and_bad_query(env, capsys):
    assert run(["series:zzzz"])[0] == 1
    assert run(["year:abc"])[0] == 2


def test_edit_needs_a_query(env):
    with pytest.raises(SystemExit):
        edit_cmd.run([])


# ------------------------------------------------------------------- ls / relocate
def do(module, argv):
    out = io.StringIO()
    return module.run(argv, out=out), out.getvalue()


def test_ls_unmatched_and_missing(env, make_cbz):
    db, (one, _, _) = env
    linked, skipped, gone = make_cbz("Linked.cbz"), make_cbz("Nope é.cbz"), make_cbz("Gone.cbz")
    with Library(db) as lib:
        lib.add_file(linked, one)
        lib.add_file(gone, one)
        lib.log_import(skipped, "skipped", 62.0)
    os.remove(gone)
    code, text = do(ls_cmd, ["--unmatched"])
    assert code == 0 and "skipped" in text and " 62.0  " in text and "Nope é.cbz" in text and "Linked" not in text
    assert do(ls_cmd, ["--unmatched", "nope", "-c"])[1].strip() == "1"
    assert do(ls_cmd, ["--unmatched", "zzz", "--json"])[1].strip() == "[]"
    code, text = do(ls_cmd, ["--missing"])
    assert text.strip().endswith("Gone.cbz") and "Linked" not in text


def test_relocate_command(env, make_cbz, tmp_path, capsys):
    db, _ = env
    path = make_cbz("A.cbz")
    with Library(db) as lib:
        lib.add_file(path)
    new = tmp_path / "moved"
    new.mkdir()
    os.replace(path, new / "A.cbz")
    code, text = do(relocate_cmd, ["-n", str(tmp_path), str(new)])
    assert code == 0 and "Would rewrite 1 file path(s)" in text and "1 of 1 file(s) exist" in text
    with Library(db) as lib:
        assert lib.get_file(path)
    code, text = do(relocate_cmd, [str(tmp_path), str(new)])
    assert "Rewrote 1" in text
    with Library(db) as lib:
        assert lib.get_file(new / "A.cbz") and lib.missing_files() == []
    assert do(relocate_cmd, ["/nowhere", "/x"])[0] == 1 and "No stored path" in capsys.readouterr().err


def test_a_relative_library_without_directory_is_explained_not_a_traceback(env, make_cbz, tmp_path, monkeypatch, capsys):
    from bdnex.ui import main

    db, (one, _, _) = env
    root = tmp_path / "comics"
    root.mkdir()
    inside = root / "A.cbz"
    inside.write_bytes(open(make_cbz("A.cbz"), "rb").read())
    with Library(db, root=root) as lib:
        lib.add_file(str(inside), one)
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))  # `directory` forgotten
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    with pytest.raises(SystemExit) as stop:
        main(["ls", "--files"])
    assert stop.value.code == 1 and "`collection_path` is not set" in capsys.readouterr().err
