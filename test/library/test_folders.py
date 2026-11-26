"""The folder tree of the review screen: counts, colours, inconsistent folders, marking a folder reviewed."""

import pytest

from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library import folders as fo
from bdnex.library.review_batch import CHECKED


def album(lib, n, series=None, title=None):
    return lib.add_album(AlbumInfo("b", str(n), title or f"T{n}", series=SeriesInfo("b", f"s-{series}", series) if series else None,
                                   volume=n if series else None))


def put(lib, path, album_id=None, checked=False):
    with lib.conn:
        file_id = lib.conn.execute("INSERT INTO files(path, album_id, md5, size, format) VALUES (?, ?, 'm', 1, 'cbz')",
                                   (path, album_id)).lastrowid
    if checked:
        lib.set_attr("file", file_id, CHECKED, "keep")
    return file_id


@pytest.fixture
def lib(tmp_path):
    return Library(":memory:", root=tmp_path)


def test_a_folder_is_green_when_confirmed_and_otherwise_takes_the_colour_of_its_worst_file(lib):
    a, b = album(lib, 1, "Tintin"), album(lib, 2, "Tintin")
    put(lib, "Series/Tintin/1.cbz", a, checked=True)
    put(lib, "Series/Tintin/2.cbz", b, checked=True)
    put(lib, "Series/Klezmer/1.cbz", a, checked=True)
    put(lib, "Series/Klezmer/2.cbz", b)
    with lib.conn:
        lib.conn.execute("INSERT INTO import_log(path, outcome, score, album_id) VALUES ('Series/Klezmer/2.cbz', 'tagged', 80.0, ?)", (b,))  # a medium match
    put(lib, "Series/Bayou/1.cbz", a)
    with lib.conn:
        lib.conn.execute("INSERT INTO import_log(path, outcome, score, album_id) VALUES ('Series/Bayou/1.cbz', 'tagged', 96.0, ?)", (a,))
    root = fo.build(fo.progress(lib))
    series = fo.find(root, "Series")
    assert (series.total, series.below_checked, series.status) == (5, 3, fo.MEDIUM)  # the worst file not confirmed is orange
    assert fo.find(root, "Series/Tintin").status == fo.DONE
    assert fo.find(root, "Series/Klezmer").status == fo.MEDIUM
    assert fo.find(root, "Series/Bayou").status == fo.AUTO  # a strong match, not confirmed yet
    assert fo.find(root, "Series/Nothing") is None
    assert [n.name for n in series.sorted_children()] == ["Bayou", "Klezmer", "Tintin"]
    assert fo.find(root, "Series/Tintin").label() == "Tintin  2/2"


def test_a_folder_with_many_children_is_shown_in_letter_groups(lib):
    for n in range(fo.MANY + 5):
        put(lib, f"OneShot/{'A' if n % 2 else 'b'}{n}/x.cbz")
    one = fo.find(fo.build(fo.progress(lib)), "OneShot")
    groups = one.shown_children()
    assert [g.name for g in groups] == ["A", "B"] and all(g.group for g in groups)
    assert sum(len(g.shown_children()) for g in groups) == fo.MANY + 5  # a group shows its folders as they are
    assert sum(g.total for g in groups) == one.total


def test_inconsistent_folders_name_the_reason(lib):
    one, two = album(lib, 1, "Tintin"), album(lib, 2, "Tintin - Divers")
    shot = album(lib, 3)
    put(lib, "Series/Tintin/1.cbz", one)
    put(lib, "Series/Tintin/2.cbz", two)
    put(lib, "Series/Mixed/1.cbz", one)
    put(lib, "Series/Mixed/2.cbz", shot)
    put(lib, "Series/Fine/1.cbz", one)
    assert fo.inconsistent(lib) == {"Series/Tintin": "2 series names", "Series/Mixed": "series and one-shots mixed"}


def test_marking_a_folder_reviewed_keeps_flagged_matches_and_touches_nothing_else(lib):
    a = album(lib, 1, "Tintin")
    flagged, free, done = put(lib, "Series/Tintin/1.cbz", a), put(lib, "Series/Tintin/2.cbz"), put(lib, "Series/Tintin/3.cbz", a, True)
    other = put(lib, "Series/Other/1.cbz", a)
    lib.flag_review(lib._resolve("Series/Tintin/1.cbz"), "doubtful")
    assert fo.mark_reviewed(lib, "Series/Tintin") == 2
    assert lib.attrs_of("file", flagged)[CHECKED] == "keep" and lib.attrs_of("file", free)[CHECKED] == "leave"
    assert lib.get_file(lib._resolve("Series/Tintin/1.cbz"))["review"] is None  # kept: the flag goes
    assert lib.get_file(lib._resolve("Series/Tintin/2.cbz"))["album_id"] is None
    assert CHECKED not in lib.attrs_of("file", other) and lib.attrs_of("file", done)[CHECKED] == "keep"
    assert fo.mark_reviewed(lib, "Series/Tintin") == 0  # again: nothing left to mark


def test_not_recursive_leaves_the_subfolders_alone(lib):
    put(lib, "Series/Tintin/1.cbz")
    put(lib, "Series/Tintin/Bonus/1.cbz")
    assert fo.mark_reviewed(lib, "Series/Tintin", recursive=False) == 1


def test_red_is_a_file_to_look_at_and_confirming_it_turns_it_green_even_without_an_album(lib):
    a = album(lib, 1, "Aama")
    for n in (1, 2):
        put(lib, f"Series/Aama/{n}.cbz", a)
        with lib.conn:
            lib.conn.execute("INSERT INTO import_log(path, outcome, score, album_id) VALUES (?, 'tagged', 95.0, ?)", (f"Series/Aama/{n}.cbz", a))
    put(lib, "Series/Loose/1.cbz")  # no album
    flagged = put(lib, "Series/Doubt/1.cbz", a)
    with lib.conn:
        lib.conn.execute("UPDATE files SET review = 'cover differs' WHERE id = ?", (flagged,))
    seen = put(lib, "Series/Seen/1.cbz")  # no album, but you said there is none online: confirmed
    lib.set_attr("file", seen, CHECKED, "leave")
    unlinked = put(lib, "Series/Unlinked/1.cbz")  # you unlinked it: that is not a confirmation
    lib.set_attr("file", unlinked, CHECKED, "unlink")
    root = fo.build(fo.progress(lib))
    assert fo.find(root, "Series/Aama").status == fo.AUTO and fo.find(root, "Series/Aama").label() == "Aama  0/2"
    assert fo.find(root, "Series/Loose").status == fo.PROBLEM and fo.find(root, "Series/Doubt").status == fo.PROBLEM
    assert fo.find(root, "Series/Seen").status == fo.DONE
    assert fo.find(root, "Series/Unlinked").status == fo.PROBLEM
    assert fo.find(root, "Series").status == fo.PROBLEM and fo.find(root, "Series").below_issues == 3
    assert fo.find(root, "Series/Loose").label() == "Loose  0/1  !1"
