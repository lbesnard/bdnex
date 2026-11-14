import os
import shutil
import zipfile

import pytest

from bdnex.config import build_settings
from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.library.hashing import file_md5
from bdnex.organizer import archive, comicinfo
from bdnex.organizer.organizer import (COPIED, COPY, FAILED, MOVE, MOVED, PRETEND, REPAIRED, SKIPPED, UNCHANGED,
                                       WRITE, WRITTEN, Organizer)

BD_CBR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bd.cbr")


class FakePrompter:
    def __init__(self, conflict="database", confirm=True, value="Anon", collision="skip"):
        self._conflict, self._confirm, self._value, self._collision = conflict, confirm, value, collision
        self.asked = []

    def conflict(self, path, diff_lines):
        self.asked.append(("conflict", diff_lines))
        return self._conflict

    def confirm(self, message):
        self.asked.append(("confirm", message))
        return self._confirm

    def ask_value(self, field, label):
        self.asked.append(("value", field))
        return self._value

    def collision(self, dest):
        self.asked.append(("collision", dest))
        return self._collision

    def corrupted_action(self, path, problems, can_move):
        self.asked.append(("corrupted", problems, can_move))
        return getattr(self, "corrupted_answer", "leave")

    def resolve_fields(self, path, changes, file_fields, db_fields):
        self.asked.append(("fields", [c.field for c in changes]))
        return getattr(self, "fields_answer", {})

    def size_action(self, path, before, after, tolerance, drawn=0):
        self.asked.append(("size", before, after))
        return getattr(self, "size_answer", "refuse")


def settings_for(tmp_path, **overrides):
    user = {"config_version": 2, "collection_path": str(tmp_path / "library"), **overrides}
    return build_settings(user)[0]


@pytest.fixture
def lib():
    with Library(":memory:") as library:
        yield library


def add_series_album(lib, source_id="1", volume=1, **kw):
    return lib.add_album(AlbumInfo(
        "bedetheque", source_id, kw.pop("title", "Redwin de la Forge"),
        series=SeriesInfo("bedetheque", "47467", "Nains"), volume=volume, year=2015, language="fr",
        credits=kw.pop("credits", {"Writer": ["Jarry, Nicolas"], "Penciller": ["Goux"]}), genres=["Fantasy"], **kw))


def add_oneshot(lib, **kw):
    return lib.add_album(AlbumInfo("bedetheque", "433432", "La bibliomule", language="fr",
                                   credits=kw.pop("credits", {"Writer": ["Lupano"]}), **kw))


def registered(lib, path, album_id):
    lib.add_file(path, album_id)
    return lib.get_file(path)


def zip_names(path):
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def comicinfo_of(path):
    with zipfile.ZipFile(path) as zf:
        return comicinfo.parse_xml(zf.read("ComicInfo.xml"))


# ---------------------------------------------------------------- write in place
def test_write_in_place_cbz(lib, make_cbz, tmp_path):
    path = make_cbz("a.cbz", pages=3)
    row = registered(lib, path, add_series_album(lib))
    before = row["md5"]
    result = Organizer(lib, settings_for(tmp_path)).process(row, WRITE)
    assert result.outcome == WRITTEN and result.dest == path
    info = comicinfo_of(path)
    assert info["Series"] == "Nains" and info["Number"] == "1" and info["PageCount"] == "3"
    assert [n for n in zip_names(path) if n.endswith(".png")] == ["page000.png", "page001.png", "page002.png"]
    after = lib.get_file(path)
    assert after["md5"] == file_md5(path) != before and after["has_metadata"] == 1  # checksum written back
    assert not os.path.exists(path + ".bdnex-tmp")


def test_second_write_is_unchanged(lib, make_cbz, tmp_path):
    path = make_cbz()
    organizer = Organizer(lib, settings_for(tmp_path))
    row = registered(lib, path, add_series_album(lib))
    organizer.process(row, WRITE)
    md5 = file_md5(path)
    assert organizer.process(lib.get_file(path), WRITE).outcome == UNCHANGED
    assert file_md5(path) == md5


def test_works_with_both_row_shapes(lib, make_cbz, tmp_path):
    path = make_cbz()
    registered(lib, path, add_series_album(lib))
    ls_row = lib.query([], files=True)[0]  # ls --files shape: id = album, file_id = file
    assert Organizer(lib, settings_for(tmp_path)).process(ls_row, WRITE).outcome == WRITTEN


def test_unmatched_corrupted_missing_files(lib, make_cbz, tmp_path, tmp_path_factory):
    org = Organizer(lib, settings_for(tmp_path))
    loose = registered(lib, make_cbz("loose.cbz"), None)
    assert org.process(loose, WRITE).outcome == SKIPPED and "import" in org.process(loose, WRITE).note
    bad = tmp_path / "bad.cbz"
    bad.write_bytes(b"nope")
    lib.add_file(str(bad), add_oneshot(lib))
    assert "can not be repaired" in org.process(lib.get_file(str(bad)), WRITE).note
    row = {**lib.get_file(str(bad)), "corrupted": 0, "path": str(tmp_path / "gone.cbz")}
    assert org.process(row, WRITE).outcome == FAILED


# -------------------------------------------------------------------- conflicts
def existing_xml_cbz(make_cbz, tmp_path, xml=b"<ComicInfo><Title>Old title</Title><Manga>No</Manga></ComicInfo>"):
    path = make_cbz("c.cbz")
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("ComicInfo.xml", xml)
    return path


@pytest.mark.parametrize("answer, expect_title", [("database", "Redwin de la Forge"), ("file", "Old title")])
def test_conflict_prompt_decides(lib, make_cbz, tmp_path, answer, expect_title):
    path = existing_xml_cbz(make_cbz, tmp_path)
    prompter = FakePrompter(conflict=answer)
    row = registered(lib, path, add_series_album(lib))
    Organizer(lib, settings_for(tmp_path), prompter).process(row, WRITE)
    assert comicinfo_of(path)["Title"] == expect_title
    kind, diff = prompter.asked[0]
    assert kind == "conflict" and any("Old title" in line for line in diff)


def test_database_wins_for_the_rest_is_asked_once(lib, make_cbz, tmp_path):
    """`database_all` answers this conflict and every later one of the run without asking again."""
    first = existing_xml_cbz(make_cbz, tmp_path)
    second = tmp_path / "other.cbz"
    shutil.copy(first, second)
    prompter = FakePrompter(conflict="database_all")
    organizer = Organizer(lib, settings_for(tmp_path), prompter)
    rows = [registered(lib, first, add_series_album(lib)), registered(lib, str(second), add_series_album(lib, "2", 2))]
    results = [organizer.process(row, WRITE) for row in rows]
    assert [r.outcome for r in results] == [WRITTEN, WRITTEN]
    assert len(prompter.asked) == 1
    assert comicinfo_of(first)["Title"] == comicinfo_of(str(second))["Title"] == "Redwin de la Forge"


def test_database_wins_for_the_rest_is_per_run(lib, make_cbz, tmp_path):
    prompter = FakePrompter(conflict="database_all")
    path = existing_xml_cbz(make_cbz, tmp_path)
    Organizer(lib, settings_for(tmp_path), prompter).process(registered(lib, path, add_series_album(lib)), WRITE)
    assert Organizer(lib, settings_for(tmp_path), FakePrompter(conflict="file"))._database_wins is False


def test_conflict_database_keeps_foreign_fields(lib, make_cbz, tmp_path):
    path = existing_xml_cbz(make_cbz, tmp_path)
    row = registered(lib, path, add_series_album(lib))
    Organizer(lib, settings_for(tmp_path, write={"conflict": "database"})).process(row, WRITE)
    info = comicinfo_of(path)
    assert info["Title"] == "Redwin de la Forge" and info["Manga"] == "No"


def test_conflict_skip_and_config_file_and_force(lib, make_cbz, tmp_path):
    path = existing_xml_cbz(make_cbz, tmp_path)
    row = registered(lib, path, add_series_album(lib))
    original = file_md5(path)
    assert Organizer(lib, settings_for(tmp_path), FakePrompter(conflict="skip")).process(row, WRITE).outcome == SKIPPED
    assert Organizer(lib, settings_for(tmp_path, write={"conflict": "file"})).process(row, WRITE).outcome == UNCHANGED
    assert file_md5(path) == original
    forced = FakePrompter()
    assert Organizer(lib, settings_for(tmp_path), forced, force=True).process(row, WRITE).outcome == WRITTEN
    assert forced.asked == [] and comicinfo_of(path)["Title"] == "Old title"  # the file's value was kept in the library


def test_unreadable_existing_xml_is_replaced(lib, make_cbz, tmp_path):
    path = existing_xml_cbz(make_cbz, tmp_path, xml=b"<<not xml")
    row = registered(lib, path, add_series_album(lib))
    assert Organizer(lib, settings_for(tmp_path)).process(row, WRITE).outcome == WRITTEN
    assert comicinfo_of(path)["Series"] == "Nains"


# ------------------------------------------------------------------ conversion
def test_cbr_converted_to_cbz_next_to_original(lib, tmp_path):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    result = Organizer(lib, settings_for(tmp_path, write={"delete_original": "yes"})).process(row, WRITE)
    dest = str(tmp_path / "old.cbz")
    assert result.outcome == WRITTEN and result.dest == dest
    assert not os.path.exists(src) and comicinfo_of(dest)["Title"] == "La bibliomule"
    assert lib.get_file(src) is None and lib.get_file(dest)["format"] == "cbz"
    assert lib.get_file(dest)["id"] == row["id"]  # same row, new path


@pytest.mark.parametrize("policy, confirm, original_survives", [
    ("no", True, True), ("yes", False, False), ("ask", True, False), ("ask", False, True)])
def test_delete_original_policy(lib, tmp_path, policy, confirm, original_survives):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    prompter = FakePrompter(confirm=confirm)
    Organizer(lib, settings_for(tmp_path, write={"delete_original": policy}), prompter).process(row, WRITE)
    assert os.path.exists(src) is original_survives
    assert os.path.exists(tmp_path / "old.cbz") and lib.get_file(str(tmp_path / "old.cbz")) is not None


def test_cbr_skipped_by_config(lib, tmp_path):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    result = Organizer(lib, settings_for(tmp_path, write={"cbr": "skip"})).process(row, WRITE)
    assert result.outcome == SKIPPED and os.path.exists(src)


def test_pdf_converted_losslessly(lib, make_image_pdf, tmp_path):
    src, jpegs = make_image_pdf(pages=2)
    row = registered(lib, src, add_oneshot(lib))
    result = Organizer(lib, settings_for(tmp_path, write={"delete_original": "no"})).process(row, WRITE)
    dest = result.dest
    assert dest.endswith(".cbz") and os.path.exists(src)  # original kept
    with zipfile.ZipFile(dest) as zf:
        assert [zf.read(n) for n in zf.namelist() if n.endswith(".jpg")] == jpegs
    assert comicinfo_of(dest)["PageCount"] == "2"


def test_pdf_skipped_by_config(lib, make_pdf, tmp_path):
    row = registered(lib, make_pdf(), add_oneshot(lib))
    assert Organizer(lib, settings_for(tmp_path, write={"pdf": "skip"})).process(row, WRITE).outcome == SKIPPED


# ---------------------------------------------------------------------- move/copy
def test_move_renames_into_library_tree(lib, make_cbz, tmp_path):
    src = make_cbz("whatever.cbz")
    row = registered(lib, src, add_series_album(lib))
    settings = settings_for(tmp_path)
    result = Organizer(lib, settings).process(row, MOVE)
    expected = str(tmp_path / "library" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz")
    assert result.outcome == MOVED and result.dest == expected
    assert os.path.exists(expected) and not os.path.exists(src)
    assert comicinfo_of(expected)["Series"] == "Nains"
    assert lib.get_file(expected)["md5"] == file_md5(expected) and lib.get_file(src) is None


def test_oneshot_template_used_for_oneshots(lib, make_cbz, tmp_path):
    row = registered(lib, make_cbz(), add_oneshot(lib))
    result = Organizer(lib, settings_for(tmp_path)).process(row, MOVE)
    assert result.dest == str(tmp_path / "library" / "OneShot" / "fr" / "La bibliomule - Lupano" /
                              "La bibliomule - Lupano.cbz")


def test_copy_keeps_source_and_database_tracks_the_copy(lib, make_cbz, tmp_path):
    src = make_cbz("keep.cbz")
    original = file_md5(src)
    row = registered(lib, src, add_series_album(lib))
    result = Organizer(lib, settings_for(tmp_path)).process(row, COPY)
    assert result.outcome == COPIED and os.path.exists(src) and file_md5(src) == original  # untouched
    assert lib.get_file(src) is None and lib.get_file(result.dest)["id"] == row["id"]


@pytest.mark.parametrize("answer, expect", [("skip", SKIPPED), ("keep_both", MOVED), ("replace", MOVED)])
def test_destination_collision(lib, make_cbz, tmp_path, answer, expect):
    settings = settings_for(tmp_path)
    org = Organizer(lib, settings, FakePrompter(collision=answer))
    a = registered(lib, make_cbz("a.cbz", pages=3), add_series_album(lib))
    first = org.process(a, MOVE)
    b = registered(lib, make_cbz("b.cbz", pages=5), add_series_album(lib, source_id="2"))  # same name again
    second = org.process(b, MOVE)
    assert second.outcome == expect
    if answer == "skip":
        assert os.path.exists(b["path"]) and lib.get_file(first.dest) is not None
    if answer == "keep_both":  # the second file is a spare version, in the spare versions folder
        assert os.sep + "_versions" + os.sep in second.dest and os.path.exists(first.dest)
        assert lib.get_file(second.dest)["spare"] == 1 and lib.get_file(first.dest)["spare"] == 0
    if answer == "replace":  # the first file goes to the trash, with a record
        assert second.dest == first.dest and lib.get_file(first.dest)["id"] == b["id"]
        assert lib.stats()["files"] == 1  # the replaced file's row is gone, not duplicated
        (entry,) = lib.trash_entries()
        assert entry["path"] == first.dest and os.path.exists(entry["trash_path"]) and "replaced by" in entry["reason"]


def test_force_keeps_both_as_spare_versions_instead_of_asking(lib, make_cbz, tmp_path):
    org = Organizer(lib, settings_for(tmp_path), FakePrompter(), force=True)
    org.process(registered(lib, make_cbz("a.cbz", pages=3), add_series_album(lib)), MOVE)
    result = org.process(registered(lib, make_cbz("b.cbz", pages=5), add_series_album(lib, source_id="2")), MOVE)
    assert os.sep + "_versions" + os.sep in result.dest and org.prompter.asked == []


def test_more_spare_versions_get_a_number(lib, make_cbz, tmp_path):
    org = Organizer(lib, settings_for(tmp_path), FakePrompter(collision="keep_both"))
    dests = []
    for number in range(4):
        row = registered(lib, make_cbz(f"v{number}.cbz", pages=3 + number), add_series_album(lib, source_id=str(number)))
        dests.append(org.process(row, MOVE).dest)
    names = [os.path.basename(d) for d in dests]
    assert names[0] == "Nains - T01 - Redwin de la Forge.cbz" and os.sep + "_versions" + os.sep not in dests[0]
    assert names[1] == names[0] and names[2].endswith("(2).cbz") and names[3].endswith("(3).cbz")
    assert [lib.get_file(d)["spare"] for d in dests] == [0, 1, 1, 1]


def test_a_spare_version_keeps_going_to_the_spare_folder_when_moved_again(lib, make_cbz, tmp_path):
    org = Organizer(lib, settings_for(tmp_path), FakePrompter(collision="keep_both"))
    org.process(registered(lib, make_cbz("a.cbz", pages=3), add_series_album(lib)), MOVE)
    spare = org.process(registered(lib, make_cbz("b.cbz", pages=5), add_series_album(lib, source_id="2")), MOVE)
    again = org.process(lib.get_file(spare.dest), MOVE)  # moved again: same place, nothing asked a second time
    assert again.outcome in (UNCHANGED, MOVED) and lib.get_file(spare.dest)["spare"] == 1


def test_the_collision_dialog_gets_the_comparison_and_its_suggestion(lib, make_cbz, tmp_path):
    seen = []

    class Chooser(FakePrompter):
        def resolve_collision(self, comparison, root=None):
            seen.append((comparison, root))
            return "skip"

    org = Organizer(lib, settings_for(tmp_path), Chooser())
    org.process(registered(lib, make_cbz("a.cbz", pages=3), add_series_album(lib)), MOVE)
    result = org.process(registered(lib, make_cbz("b.cbz", pages=9), add_series_album(lib, source_id="2")), MOVE)
    (comparison, root), = seen
    assert result.outcome == SKIPPED and comparison.suggestion == "keep_new" and "9 pages against 3" in comparison.reason
    assert comparison.new.pages == 9 and comparison.existing[0].pages == 3 and root == lib.root


def test_in_place_writing_keeps_both_by_numbering_beside_the_original(lib, make_cbz, tmp_path):
    folder = tmp_path / "dl"
    folder.mkdir()
    a = make_cbz("x.cbz", pages=3)
    shutil.copy(a, folder / "same.cbz")
    shutil.copy(BD_CBR, folder / "same.cbr")
    one = registered(lib, str(folder / "same.cbz"), add_series_album(lib))
    two = registered(lib, str(folder / "same.cbr"), add_series_album(lib, source_id="2"))
    org = Organizer(lib, settings_for(tmp_path), FakePrompter(collision="keep_both", confirm=False))
    result = org.process(two, WRITE)  # same.cbr -> same.cbz, which exists
    assert result.dest == str(folder / "same (2).cbz") and lib.get_file(result.dest)["spare"] == 0


# ---------------------------------------------------------------- missing fields
def test_missing_field_asks_and_saves_to_database(lib, make_cbz, tmp_path):
    album_id = add_oneshot(lib, credits={})
    prompter = FakePrompter(value="Jane Doe")
    result = Organizer(lib, settings_for(tmp_path), prompter).process(registered(lib, make_cbz(), album_id), MOVE)
    assert "Jane Doe" in result.dest and prompter.asked == [("value", "authors")]  # asked once, used twice
    assert lib.get_album(album_id)["credits"] == {"Writer": ["Jane Doe"]}
    assert "Jane Doe" in comicinfo_of(result.dest).get("Writer", "")  # the file got it too


def test_missing_field_placeholder_and_skip_and_empty_answer(lib, make_cbz, tmp_path):
    album_id = add_oneshot(lib, credits={})
    row = registered(lib, make_cbz(), album_id)
    placeholder = Organizer(lib, settings_for(tmp_path, paths={"missing_field": "placeholder", "placeholder": "Inconnu"}))
    assert "Inconnu" in placeholder.process(row, MOVE).dest
    row = registered(lib, make_cbz("b.cbz", pages=9), album_id)
    skip = Organizer(lib, settings_for(tmp_path, paths={"missing_field": "skip"})).process(row, MOVE)
    assert skip.outcome == SKIPPED and "authors" in skip.note
    empty = Organizer(lib, settings_for(tmp_path), FakePrompter(value="")).process(row, MOVE)
    assert empty.outcome == SKIPPED


def test_missing_year_persisted_as_integer(lib, make_cbz, tmp_path):
    album_id = add_series_album(lib)
    org = Organizer(lib, settings_for(tmp_path, paths={"series": "$series/$year/$title"}), FakePrompter(value="1999"))
    lib.set_album_fields(album_id, year=None)
    org.process(registered(lib, make_cbz(), album_id), MOVE)
    assert lib.get_album(album_id)["year"] == 1999


# ---------------------------------------------------------------------- safety
def test_pretend_changes_nothing(lib, make_cbz, tmp_path):
    src = make_cbz()
    row = registered(lib, src, add_series_album(lib))
    md5 = file_md5(src)
    prompter = FakePrompter()
    result = Organizer(lib, settings_for(tmp_path), prompter, pretend=True).process(row, MOVE)
    assert result.outcome == PRETEND and "move to" in result.note
    assert file_md5(src) == md5 and not (tmp_path / "library").exists()
    assert lib.get_file(src)["md5"] == md5 and prompter.asked == []


def test_failed_verification_leaves_original_and_no_temp_file(lib, make_cbz, tmp_path, monkeypatch):
    src = make_cbz()
    md5 = file_md5(src)
    row = registered(lib, src, add_series_album(lib))

    def boom(*a, **k):
        raise archive.ArchiveError("mismatch")

    monkeypatch.setattr(archive, "verify_cbz", boom)
    result = Organizer(lib, settings_for(tmp_path)).run([row], MOVE)[0]
    assert result.outcome == FAILED and "mismatch" in result.note
    assert file_md5(src) == md5 and lib.get_file(src) is not None
    leftovers = [n for _, _, files in os.walk(tmp_path) for n in files if n.endswith(".bdnex-tmp")]
    assert leftovers == []


def test_batch_continues_after_a_failure(lib, make_cbz, tmp_path):
    good = registered(lib, make_cbz("good.cbz"), add_series_album(lib))
    broken = {**good, "path": str(tmp_path / "ghost.cbz")}
    results = Organizer(lib, settings_for(tmp_path)).run([broken, good], WRITE)
    assert [r.outcome for r in results] == [FAILED, WRITTEN]


def test_move_needs_a_real_library_directory(lib, make_cbz):
    from bdnex.config import ConfigError
    settings = build_settings({"config_version": 2})[0]  # placeholder directory
    row = registered(lib, make_cbz(), add_series_album(lib))
    results = Organizer(lib, settings).run([row], MOVE)
    assert results[0].outcome == FAILED and "collection_path" in results[0].note


# ------------------------------------------------------------- damaged archives
def damaged_row(lib, path, album_id=None):
    return registered(lib, path, album_id if album_id is not None else add_series_album(lib))


def test_write_repairs_a_page_with_a_wrong_checksum_then_tags(lib, make_damaged_cbz, tmp_path):
    path, originals = make_damaged_cbz()
    row = damaged_row(lib, path)
    assert row["corrupted"] == 1
    result = Organizer(lib, settings_for(tmp_path)).process(row, WRITE)
    assert result.outcome == WRITTEN and "repair 1 page(s)" in result.note
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None  # clean now
        damaged = zf.read("p01.jpg")
        assert zf.read("p00.jpg") == originals["p00.jpg"] and zf.read("p02.jpg") == originals["p02.jpg"]
    assert len(damaged) == len(originals["p01.jpg"])  # same data the damaged file gave: nothing else lost
    assert comicinfo_of(path)["Series"] == "Nains"
    assert lib.get_file(path)["corrupted"] == 0 and lib.get_file(path)["md5"] == file_md5(path)


def test_repair_command_rebuilds_without_adding_tags(lib, make_damaged_cbz, tmp_path):
    path, originals = make_damaged_cbz(comicinfo=b"<ComicInfo><Title>Mine</Title></ComicInfo>")
    row = damaged_row(lib, path)
    result = Organizer(lib, settings_for(tmp_path)).repair(row)
    assert result.outcome == REPAIRED and "p01.jpg" in result.note
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None and zf.read("ComicInfo.xml") == b"<ComicInfo><Title>Mine</Title></ComicInfo>"
    assert lib.get_file(path)["corrupted"] == 0


def test_repair_pretend_and_healthy_files(lib, make_damaged_cbz, make_cbz, tmp_path):
    path, _ = make_damaged_cbz()
    before = file_md5(path)
    row = damaged_row(lib, path)
    pretend = Organizer(lib, settings_for(tmp_path), pretend=True).repair(row)
    assert pretend.outcome == PRETEND and file_md5(path) == before
    healthy = registered(lib, make_cbz(), add_oneshot(lib))
    assert Organizer(lib, settings_for(tmp_path)).repair(healthy).outcome == UNCHANGED


def test_move_repairs_before_moving(lib, make_damaged_cbz, tmp_path):
    path, _ = make_damaged_cbz()
    result = Organizer(lib, settings_for(tmp_path)).process(damaged_row(lib, path), MOVE)
    assert result.outcome == MOVED
    with zipfile.ZipFile(result.dest) as zf:
        assert zf.testzip() is None
    assert not os.path.exists(path)


@pytest.mark.parametrize("answer, mode, expect, source_stays, dest_exists", [
    ("leave", MOVE, SKIPPED, True, False),
    ("move", MOVE, MOVED, False, True),
    ("move", COPY, COPIED, True, True)])
def test_unrepairable_asks_then_leaves_or_moves_as_is(lib, make_damaged_cbz, tmp_path, answer, mode, expect,
                                                      source_stays, dest_exists):
    path, _ = make_damaged_cbz(damage="junk")
    original = file_md5(path)
    prompter = FakePrompter()
    prompter.corrupted_answer = answer
    result = Organizer(lib, settings_for(tmp_path), prompter).process(damaged_row(lib, path), mode)
    assert result.outcome == expect and "can not be repaired" in result.note and "p01.jpg" in result.note
    assert prompter.asked[0][0] == "corrupted" and prompter.asked[0][2] is True  # moving was offered
    assert os.path.exists(path) is source_stays
    expected_dest = tmp_path / "library" / "Series" / "Nains" / "Nains - T01 - Redwin de la Forge.cbz"
    assert expected_dest.exists() is dest_exists
    if dest_exists:
        assert file_md5(str(expected_dest)) == original  # moved as it is: no tags, no repair
        assert lib.get_file(str(expected_dest)) is not None and lib.get_file(str(expected_dest))["corrupted"] == 1


def test_unrepairable_in_write_mode_can_only_be_left(lib, make_damaged_cbz, tmp_path):
    path, _ = make_damaged_cbz(damage="junk")
    prompter = FakePrompter()
    prompter.corrupted_answer = "move"
    result = Organizer(lib, settings_for(tmp_path), prompter).process(damaged_row(lib, path), WRITE)
    assert result.outcome == SKIPPED and prompter.asked[0][2] is False  # `move` was not even offered
    assert os.path.exists(path)


@pytest.mark.parametrize("setting, kwargs, expect", [
    ("leave", {}, SKIPPED), ("move", {}, MOVED), ("ask", {"force": True}, SKIPPED), ("ask", {"pretend": True}, PRETEND)])
def test_corrupted_setting_decides_without_asking(lib, make_damaged_cbz, tmp_path, setting, kwargs, expect):
    path, _ = make_damaged_cbz(damage="junk")
    prompter = FakePrompter()
    result = Organizer(lib, settings_for(tmp_path, write={"corrupted": setting}), prompter, **kwargs).process(
        damaged_row(lib, path), MOVE)
    assert result.outcome == expect and prompter.asked == []
    if kwargs.get("pretend"):
        assert os.path.exists(path) and not (tmp_path / "library").exists()


def test_repair_command_reports_what_it_cannot_fix(lib, make_damaged_cbz, tmp_path):
    path, _ = make_damaged_cbz(damage="junk")
    before = file_md5(path)
    result = Organizer(lib, settings_for(tmp_path)).repair(damaged_row(lib, path))
    assert result.outcome == FAILED and "p01.jpg" in result.note and file_md5(path) == before


def test_preview_of_a_conversion_in_place_does_not_say_move(lib, tmp_path):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    result = Organizer(lib, settings_for(tmp_path), pretend=True).process(row, WRITE)
    assert result.outcome == PRETEND and "save as old.cbz (same folder)" in result.note
    assert "move" not in result.note and os.path.exists(src)
    moved = Organizer(lib, settings_for(tmp_path), pretend=True).process(row, MOVE)
    assert "move to " in moved.note  # a real move still says so


# ---------------------------------------- the database never erases what it does not know
def file_with_extra_fields(make_cbz):
    return existing_xml_cbz(make_cbz, None, xml=(b"<ComicInfo><Title>Old title</Title><Format>One Shot</Format>"
                                                 b"<Writer>Someone Else</Writer><Manga>No</Manga></ComicInfo>"))


def test_choosing_the_database_keeps_fields_it_has_no_value_for(lib, make_cbz, tmp_path):
    path = file_with_extra_fields(make_cbz)
    album_id = lib.add_album(AlbumInfo("bedetheque", "7", "New title", credits={}))  # no writer, no format
    row = registered(lib, path, album_id)
    prompter = FakePrompter(conflict="database")
    Organizer(lib, settings_for(tmp_path), prompter).process(row, WRITE)
    info = comicinfo_of(path)
    assert info["Title"] == "New title"  # the database had a value: it wins
    assert info["Writer"] == "Someone Else" and info["Format"] == "One Shot" and info["Manga"] == "No"  # kept
    [(kind, diff)] = prompter.asked
    assert all("Writer" not in line and "Format" not in line for line in diff)  # nothing about them was asked


def test_nothing_to_decide_means_no_question_and_no_rewrite(lib, make_cbz, tmp_path):
    """The file already holds everything the database knows, plus a field only the file has."""
    path = make_cbz("c.cbz", pages=3)
    album_id = add_series_album(lib)
    fields = comicinfo.album_to_fields(lib.get_album(album_id), page_count=3)
    xml = comicinfo.build_xml({**fields, "Format": "One Shot"})
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("ComicInfo.xml", xml)
    row = registered(lib, path, album_id)
    before = file_md5(path)
    prompter = FakePrompter()
    result = Organizer(lib, settings_for(tmp_path), prompter).process(row, WRITE)
    assert result.outcome == UNCHANGED and prompter.asked == []
    assert file_md5(path) == before and comicinfo_of(path)["Format"] == "One Shot"


# ----------------------------------------------------- what is chosen goes into the library
def conflicted(lib, make_cbz, tmp_path):
    path = make_cbz("a.cbz", pages=2, comicinfo=True)  # its ComicInfo.xml names another title
    album_id = add_series_album(lib)
    return path, registered(lib, path, album_id), album_id


def test_field_by_field_takes_the_typed_value_and_keeps_it_in_the_library(lib, make_cbz, tmp_path):
    path, row, album_id = conflicted(lib, make_cbz, tmp_path)
    prompter = FakePrompter(conflict="fields")
    prompter.fields_answer = {"Title": "My own title"}
    result = Organizer(lib, settings_for(tmp_path), prompter).process(row, WRITE)
    assert result.outcome == WRITTEN and comicinfo_of(path)["Title"] == "My own title"
    album = lib.get_album(album_id)
    assert album["title"] == "My own title" and album["extra"]["edits"]["title"] == "My own title"
    assert album["series"] == "Nains"  # the rest is the fetched data, untouched


def test_choosing_the_file_makes_the_library_take_its_values(lib, make_cbz, tmp_path):
    path, row, album_id = conflicted(lib, make_cbz, tmp_path)
    title_in_file = comicinfo_of(path)["Title"]
    Organizer(lib, settings_for(tmp_path), FakePrompter(conflict="file")).process(row, WRITE)
    assert comicinfo_of(path)["Title"] == title_in_file
    assert lib.get_album(album_id)["title"] == title_in_file


def test_choosing_the_database_changes_nothing_in_the_library(lib, make_cbz, tmp_path):
    path, row, album_id = conflicted(lib, make_cbz, tmp_path)
    Organizer(lib, settings_for(tmp_path), FakePrompter(conflict="database")).process(row, WRITE)
    assert "edits" not in lib.get_album(album_id)["extra"] and lib.get_album(album_id)["title"] == "Redwin de la Forge"


def test_a_preview_stores_nothing(lib, make_cbz, tmp_path):
    path, row, album_id = conflicted(lib, make_cbz, tmp_path)
    Organizer(lib, settings_for(tmp_path), FakePrompter(conflict="file"), pretend=True).process(row, WRITE)
    assert "edits" not in lib.get_album(album_id)["extra"]


def test_skipping_in_the_field_by_field_dialog_skips_the_file(lib, make_cbz, tmp_path):
    path, row, album_id = conflicted(lib, make_cbz, tmp_path)
    prompter = FakePrompter(conflict="fields")
    prompter.fields_answer = None
    assert Organizer(lib, settings_for(tmp_path), prompter).process(row, WRITE).outcome == SKIPPED


def test_fields_to_changes():
    changes = comicinfo.fields_to_changes({"Title": "T", "Year": "2020", "Number": "4", "Writer": "A, B",
                                           "Genre": "Fantasy, Humour", "Series": "X", "PageCount": "9"})
    assert changes == {"title": "T", "year": 2020, "volume": 4, "credits": {"Writer": ["A", "B"]},
                       "genres": ["Fantasy", "Humour"]}


# ------------------------------------------------------------------- the size check
@pytest.fixture
def strict_size(monkeypatch):
    """Any size difference counts (the files of the tests are tiny)."""
    monkeypatch.setattr("bdnex.organizer.organizer.MIN_SIZE_CHANGE", 0)


def test_small_differences_are_never_a_problem(lib, make_cbz, tmp_path):
    path = make_cbz(pages=3)  # tags add a few hundred bytes to a tiny file: far under the 1 MiB floor
    row = registered(lib, path, add_series_album(lib))
    assert Organizer(lib, settings_for(tmp_path)).process(row, WRITE).outcome == WRITTEN


def test_a_size_off_by_more_than_the_tolerance_refuses_and_leaves_the_original(lib, make_cbz, tmp_path, strict_size):
    path = make_cbz(pages=3)
    original = open(path, "rb").read()
    row = registered(lib, path, add_series_album(lib))
    result = Organizer(lib, settings_for(tmp_path, write={"size_tolerance": 0.001, "size_change": "refuse"})
                       ).process(row, WRITE)
    assert result.outcome == SKIPPED and "size" in result.note and "untouched" in result.note
    assert open(path, "rb").read() == original and not os.path.exists(path + ".bdnex-tmp")


def test_accept_goes_on_and_a_tolerance_of_zero_turns_the_check_off(lib, make_cbz, tmp_path, strict_size):
    path = make_cbz("a.cbz", pages=3)
    row = registered(lib, path, add_series_album(lib))
    accepted = Organizer(lib, settings_for(tmp_path, write={"size_tolerance": 0.001, "size_change": "accept"}))
    result = accepted.process(row, WRITE)
    assert result.outcome == WRITTEN and "accepted" in result.note
    other = make_cbz("b.cbz", pages=3)
    row = registered(lib, other, add_series_album(lib, source_id="2", volume=2))
    assert Organizer(lib, settings_for(tmp_path, write={"size_tolerance": 0})).process(row, WRITE).outcome == WRITTEN


def test_ask_puts_the_question_and_follows_the_answer(lib, make_cbz, tmp_path, strict_size):
    settings = settings_for(tmp_path, write={"size_tolerance": 0.001})
    path = make_cbz("a.cbz", pages=3)
    row = registered(lib, path, add_series_album(lib))
    refusing = FakePrompter()
    assert Organizer(lib, settings, refusing).process(row, WRITE).outcome == SKIPPED
    assert refusing.asked and refusing.asked[0][0] == "size"
    accepting = FakePrompter()
    accepting.size_answer = "accept"
    assert Organizer(lib, settings, accepting).process(row, WRITE).outcome == WRITTEN
    # nobody to ask, or --force: the safe answer without a prompter, the stated one with --force
    other = make_cbz("c.cbz", pages=3)
    row = registered(lib, other, add_series_album(lib, source_id="3", volume=3))
    assert Organizer(lib, settings).process(row, WRITE).outcome == SKIPPED
    assert Organizer(lib, settings, force=True).process(row, WRITE).outcome == WRITTEN


def test_the_original_is_never_deleted_if_the_new_archive_fails_the_final_check(lib, tmp_path, monkeypatch):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    real, calls = archive.verify_cbz, []

    def verify(dest, entries, xml):
        calls.append(dest)
        if not dest.endswith(".bdnex-tmp"):  # the first check (the temporary file) passes, the final one does not
            raise archive.ArchiveError("a page differs")
        return real(dest, entries, xml)

    monkeypatch.setattr(archive, "verify_cbz", verify)
    result = Organizer(lib, settings_for(tmp_path, write={"delete_original": "yes"})).process(row, WRITE)
    assert result.outcome == FAILED and "original is kept" in result.note
    assert os.path.exists(src) and not os.path.exists(tmp_path / "old.cbz")
    assert lib.get_file(src) is not None
    assert len(calls) == 2


def test_a_good_conversion_is_checked_twice(lib, tmp_path, monkeypatch):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    real, calls = archive.verify_cbz, []
    monkeypatch.setattr(archive, "verify_cbz", lambda *a: calls.append(a[0]) or real(*a))
    assert Organizer(lib, settings_for(tmp_path, write={"delete_original": "yes"})).process(row, WRITE).outcome == WRITTEN
    assert [c.endswith(".bdnex-tmp") for c in calls] == [True, False]


# --------------------------------------------------------- trash for originals, and free space
def test_a_deleted_original_goes_to_the_trash_and_is_recorded(lib, tmp_path):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    album_id = add_oneshot(lib)
    row = registered(lib, src, album_id)
    old_md5 = row["md5"]
    result = Organizer(lib, settings_for(tmp_path, write={"delete_original": "yes"})).process(row, WRITE)
    assert result.outcome == WRITTEN and "moved to the trash" in result.note
    assert not os.path.exists(src)
    (entry,) = lib.trash_entries()
    assert os.path.exists(entry["trash_path"]) and entry["path"] == src
    assert entry["album_id"] == album_id and entry["md5"] == old_md5 and entry["format"] == "cbr"
    assert "converted to old.cbz" in entry["reason"]
    dest = str(tmp_path / "old.cbz")
    assert lib.get_file(dest)["album_id"] == album_id and lib.get_file(src) is None  # the library follows the new file


def test_without_the_trash_the_original_is_deleted_for_good(lib, tmp_path):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    Organizer(lib, settings_for(tmp_path, write={"delete_original": "yes", "use_trash": False})).process(row, WRITE)
    assert not os.path.exists(src) and lib.trash_entries() == []


def test_a_kept_original_is_not_trashed(lib, tmp_path):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    Organizer(lib, settings_for(tmp_path, write={"delete_original": "no"})).process(row, WRITE)
    assert os.path.exists(src) and lib.trash_entries() == []


def test_a_trashed_original_can_be_restored(lib, tmp_path):
    from bdnex import trash
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    settings = settings_for(tmp_path, write={"delete_original": "yes"})
    Organizer(lib, settings).process(row, WRITE)
    (entry,) = lib.trash_entries()
    assert trash.restore(lib, entry["id"]) == src and os.path.exists(src)


def test_not_enough_room_writes_nothing(lib, tmp_path, monkeypatch):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    monkeypatch.setattr("bdnex.organizer.organizer.free_space", lambda path: 10 * 1024 * 1024)  # 10 MiB free
    result = Organizer(lib, settings_for(tmp_path, write={"delete_original": "yes"})).process(row, WRITE)
    assert result.outcome == FAILED and "not enough free space" in result.note and "min_free_space" in result.note
    assert os.path.exists(src) and not os.path.exists(tmp_path / "old.cbz") and not os.path.exists(str(tmp_path / "old.cbz.bdnex-tmp"))


def test_the_disk_of_the_database_counts_too(tmp_path, monkeypatch):
    with Library(tmp_path / "db" / "l.sqlite") as library:
        src = str(tmp_path / "old.cbr")
        shutil.copy(BD_CBR, src)
        row = registered(library, src, add_oneshot(library))
        monkeypatch.setattr("bdnex.organizer.organizer.free_space",
                            lambda path: 10**12 if str(path).endswith(".cbz") else 1024)
        result = Organizer(library, settings_for(tmp_path)).process(row, WRITE)
        assert result.outcome == FAILED and "database" in result.note


def test_min_free_space_zero_turns_the_check_off(lib, tmp_path, monkeypatch):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    monkeypatch.setattr("bdnex.organizer.organizer.free_space", lambda path: 0)
    assert Organizer(lib, settings_for(tmp_path, write={"min_free_space": 0, "delete_original": "no"})
                     ).process(row, WRITE).outcome == WRITTEN


def test_a_disk_that_fills_up_midway_leaves_the_original_and_no_debris(lib, make_cbz, tmp_path, monkeypatch):
    import errno
    path = make_cbz("a.cbz", pages=3)
    original = open(path, "rb").read()
    row = registered(lib, path, add_series_album(lib))

    def full(dest, entries, xml):
        open(dest, "wb").write(b"partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(archive, "write_cbz", full)
    result = Organizer(lib, settings_for(tmp_path)).process(row, WRITE)
    assert result.outcome == FAILED and "disk is full" in result.note
    assert open(path, "rb").read() == original and not os.path.exists(path + ".bdnex-tmp")


# ------------------------------------------------------------------ format_preferred
def test_format_none_converts_nothing(lib, tmp_path):
    src = str(tmp_path / "old.cbr")
    shutil.copy(BD_CBR, src)
    row = registered(lib, src, add_oneshot(lib))
    original = open(src, "rb").read()
    result = Organizer(lib, settings_for(tmp_path, write={"format_preferred": "none", "delete_original": "yes"})
                       ).process(row, WRITE)
    assert result.outcome == SKIPPED and "format_preferred" in result.note
    assert open(src, "rb").read() == original and not os.path.exists(tmp_path / "old.cbz")


def test_format_none_still_tags_a_cbz(lib, make_cbz, tmp_path):
    path = make_cbz("a.cbz", pages=2)
    row = registered(lib, path, add_series_album(lib))
    assert Organizer(lib, settings_for(tmp_path, write={"format_preferred": "none"})).process(row, WRITE
                                                                                          ).outcome == WRITTEN


def test_format_none_also_leaves_the_pdf(lib, make_pdf, tmp_path):
    pdf = make_pdf("p.pdf")
    row = registered(lib, pdf, add_oneshot(lib))
    organizer = Organizer(lib, settings_for(tmp_path, write={"format_preferred": "none"}))
    assert organizer.process(row, WRITE).outcome == SKIPPED and os.path.exists(pdf)


def test_format_none_does_not_repair_a_cbr_into_a_cbz(lib, tmp_path):
    src = str(tmp_path / "damaged.cbr")
    shutil.copy(BD_CBR, src)
    organizer = Organizer(lib, settings_for(tmp_path, write={"format_preferred": "none"}))
    result = organizer.repair({"path": src, "format": "cbr", "corrupted": 1})
    assert result.outcome == SKIPPED and not os.path.exists(tmp_path / "damaged.cbz")


def test_pdf_and_cbr_have_their_own_switches_under_cbz(lib, make_pdf, tmp_path):
    pdf = make_pdf("p.pdf")
    row = registered(lib, pdf, add_oneshot(lib))
    settings = settings_for(tmp_path, write={"pdf": "skip", "cbr": "convert"})
    assert Organizer(lib, settings).process(row, WRITE).outcome == SKIPPED




# ----------------------------------------------------------------------------- abort
def test_aborting_stops_the_run_and_leaves_the_other_files_alone(lib, make_cbz, tmp_path):
    from bdnex.organizer.organizer import WriteAborted

    class Aborting(FakePrompter):
        def conflict(self, path, diff_lines):
            raise WriteAborted()

    first = registered(lib, make_cbz("a.cbz", pages=2), add_series_album(lib))
    clash = existing_xml_cbz(make_cbz, tmp_path)
    second = registered(lib, clash, add_series_album(lib, source_id="2", volume=2))
    third_path = make_cbz("z.cbz", pages=2)
    third = registered(lib, third_path, add_series_album(lib, source_id="3", volume=3))
    original = open(third_path, "rb").read()
    organizer = Organizer(lib, settings_for(tmp_path), Aborting())
    results = organizer.run([first, second, third], WRITE)
    assert [r.outcome for r in results] == [WRITTEN]  # the first was done, the second aborted, the third untouched
    assert organizer.aborted_at == clash and open(third_path, "rb").read() == original


def test_the_abort_key_of_a_real_question_raises_it(monkeypatch):
    import InquirerPy.inquirer as inquirer
    from bdnex.organizer.organizer import WriteAborted
    from bdnex.ui.prompts import InquirerPrompter

    seen = {}

    class Answer:
        def __init__(self, **kw):
            seen.update(kw)

        def register_kb(self, *keys):
            return lambda handler: handler

        def execute(self):
            return InquirerPrompter.ABORT

    monkeypatch.setattr(inquirer, "select", lambda **kw: Answer(**kw))
    with pytest.raises(WriteAborted):
        InquirerPrompter().size_action("/x/a.cbr", 10, 99, 20)
    assert seen["choices"][-1]["name"].startswith("[A]bort the run")


def test_the_report_says_where_it_stopped_and_how_to_continue():
    from bdnex.ui.write_cmd import aborted_message
    assert "Aborted at /x/a.cbz" in aborted_message("/x/a.cbz") and "bdnex write" in aborted_message("/x/a.cbz")


def test_the_report_says_how_many_pdf_pages_had_to_be_drawn(lib, make_pdf, make_image_pdf, tmp_path):
    drawn = make_pdf("text.pdf", pages=2)
    row = registered(lib, drawn, add_oneshot(lib))
    result = Organizer(lib, settings_for(tmp_path)).process(row, WRITE)
    assert "2 of 2 page(s) had to be drawn" in result.note
    scan, _ = make_image_pdf("scan.pdf", pages=3)
    row = registered(lib, scan, add_series_album(lib))
    result = Organizer(lib, settings_for(tmp_path)).process(row, WRITE)
    assert "all 3 pages extracted untouched" in result.note




def test_the_report_counts_pictures_saved_as_png_without_loss(lib, make_image_pdf, tmp_path, monkeypatch):
    from bdnex.organizer import archive as arch
    monkeypatch.setattr(arch, "PDF_EXTRACTABLE", {"png"})  # the test PDF holds JPEGs: treat them as unopenable
    scan, _ = make_image_pdf("scan.pdf", pages=2)
    row = registered(lib, scan, add_oneshot(lib))
    result = Organizer(lib, settings_for(tmp_path)).process(row, WRITE)
    assert "extracted without loss (2 saved as PNG, same pixels)" in result.note


def test_a_preview_does_not_read_the_pictures(lib, make_image_pdf, tmp_path, monkeypatch):
    from bdnex.organizer import archive as arch
    def boom(*a, **k):
        raise AssertionError("a preview must not read the whole archive")
    monkeypatch.setattr(arch, "read_entries", boom)
    scan, _ = make_image_pdf("scan.pdf", pages=2)
    row = registered(lib, scan, add_oneshot(lib))
    result = Organizer(lib, settings_for(tmp_path), pretend=True).process(row, WRITE)
    assert result.outcome == "pretend" and "convert pdf -> cbz" in result.note


def test_path_rules_give_some_albums_another_template(lib, make_cbz, tmp_path):
    from bdnex.config import PathRule
    row = registered(lib, make_cbz("a.cbz"), add_series_album(lib))
    settings = settings_for(tmp_path)
    series = lib.get_album(row["album_id"])["series"]
    plain = Organizer(lib, settings, pretend=True).process(row, MOVE)
    settings.paths.rules = [PathRule(match=f"series:{series[:4]}", template="Collections/$series/$title"),
                            PathRule(match=["series:zzz"], template="Never")]
    ruled = Organizer(lib, settings, pretend=True).process(row, MOVE)
    assert "/Collections/" in ruled.dest and "/Collections/" not in plain.dest
    settings.paths.rules = [PathRule(match=["series:zzz"], template="Never")]
    assert Organizer(lib, settings, pretend=True).process(row, MOVE).dest == plain.dest


def test_a_move_removes_the_folder_it_empties_even_with_junk_left(lib, make_cbz, tmp_path):
    settings = settings_for(tmp_path)
    root = settings.require_collection()
    old = root / "Old" / "Deep"
    old.mkdir(parents=True)
    src = make_cbz("a.cbz")
    moved = old / "a.cbz"
    shutil.move(str(src), moved)
    (old / ".DS_Store").write_bytes(b"x")
    (old / "._a.cbz").write_bytes(b"x")
    row = registered(lib, str(moved), add_series_album(lib))
    result = Organizer(lib, settings).process(row, MOVE)
    assert result.outcome == "moved"
    assert not (root / "Old").exists() and root.exists()  # both folders gone, the library root stays


def test_a_move_keeps_a_folder_that_still_holds_something(lib, make_cbz, tmp_path):
    settings = settings_for(tmp_path)
    root = settings.require_collection()
    old = root / "Old"
    old.mkdir(parents=True)
    moved = old / "a.cbz"
    shutil.move(str(make_cbz("a.cbz")), moved)
    (old / "notes.txt").write_text("keep")
    row = registered(lib, str(moved), add_series_album(lib))
    Organizer(lib, settings).process(row, MOVE)
    assert (old / "notes.txt").exists()


def test_after_a_hand_edit_move_renames_the_file_and_removes_the_old_folder(lib, make_cbz, tmp_path):
    settings = settings_for(tmp_path)
    root = settings.require_collection()
    album_id = add_series_album(lib)
    row = registered(lib, make_cbz("a.cbz"), album_id)
    placed = Organizer(lib, settings).process(row, MOVE)
    old_dir = os.path.dirname(placed.dest)
    lib.edit_album(album_id, {"title": "Totally Other Title"})
    again = Organizer(lib, settings).process(lib.get_file(placed.dest), MOVE)
    assert again.outcome == "moved" and "Totally Other Title" in again.dest
    assert os.path.exists(again.dest) and not os.path.exists(placed.dest)
    assert lib.get_file(again.dest) is not None


def test_move_still_places_a_file_that_is_left_in_its_format(lib, make_cbz, tmp_path):
    settings = settings_for(tmp_path)
    settings.write.format_preferred = "none"
    root = settings.require_collection()
    src = tmp_path / "inbox" / "x.cbr"
    src.parent.mkdir()
    src.write_bytes(b"Rar!\x1a\x07\x00" + b"\0" * 64)  # looks like a RAR; it is never opened
    row = registered(lib, str(src), add_series_album(lib))
    assert row["format"] == "cbr"
    assert Organizer(lib, settings).process(row, WRITE).outcome == "skipped"  # `write` has nothing to do
    result = Organizer(lib, settings).process(row, MOVE)
    assert result.outcome == "moved" and result.dest.endswith(".cbr")  # its own extension: still a RAR
    assert os.path.exists(result.dest) and not src.exists()
    assert lib.get_file(result.dest)["format"] == "cbr"


def test_a_damaged_file_moved_as_it_is_into_a_taken_name_does_not_crash(lib, make_cbz, tmp_path):
    settings = settings_for(tmp_path)
    settings.write.corrupted = "move"
    album = add_series_album(lib)
    first = Organizer(lib, settings).process(registered(lib, make_cbz("a.cbz"), album), MOVE)
    bad = tmp_path / "bad.cbz"
    bad.write_bytes(b"not an archive")
    row = {**registered(lib, str(bad), album), "corrupted": 1}
    result = Organizer(lib, settings, force=True).process(row, MOVE)
    assert result.outcome in ("moved", "skipped") and os.path.exists(first.dest)


def test_a_preview_says_when_the_destination_is_taken(lib, make_cbz, tmp_path):
    settings = settings_for(tmp_path)
    album = add_series_album(lib)
    first = Organizer(lib, settings).process(registered(lib, make_cbz("a.cbz"), album), MOVE)
    other = registered(lib, make_cbz("b.cbz"), album)
    note = Organizer(lib, settings, pretend=True).process(other, MOVE).note
    assert "destination already exists" in note


# ----------------------------------------------------------------- several files at once
@pytest.fixture
def shared(tmp_path, make_cbz):
    """A library in a real file (workers open their own connection to it), its settings, and six files to move."""
    settings = settings_for(tmp_path, library=str(tmp_path / "shared.sqlite"))
    library = Library.for_settings(settings)
    rows = []
    for n in range(1, 7):
        info = AlbumInfo("bedetheque", f"7{n}", f"Titre {n}", series=SeriesInfo("bedetheque", "70", "Foo"), volume=n, language="fr")
        rows.append(registered(library, make_cbz(f"in{n}.cbz", pages=3 + n), library.add_album(info)))
    yield settings, library, rows
    library.close()


def test_several_files_are_worked_on_at_once_with_the_same_result_as_one_by_one(shared):
    settings, lib, rows = shared
    seen = []
    results = Organizer(lib, settings, force=True).run(rows, MOVE, workers=3, on_result=lambda n, t, r: seen.append((n, t, r.outcome)))
    assert [r.outcome for r in results] == ["moved"] * 6 and [n for n, _, _ in seen] == [1, 2, 3, 4, 5, 6]  # in order
    assert all(r.seconds > 0 for r in results)
    for row, result in zip(rows, results):
        assert os.path.exists(result.dest) and not os.path.exists(row["path"])
        assert lib.get_file(result.dest) is not None and lib.get_file(row["path"]) is None  # the library followed
        assert "ComicInfo.xml" in zip_names(result.dest)
    assert len(lib.batches()) == 1 and lib.batches()[0]["moves"] == 6  # one undoable run


def test_files_with_the_same_destination_are_not_written_at_once(shared, make_cbz):
    settings, lib, rows = shared
    album = add_series_album(lib)
    same = [registered(lib, make_cbz(f"same{n}.cbz", pages=3 + n), album) for n in range(3)]  # one album, three files
    organizer = Organizer(lib, settings, force=True)
    assert organizer._independent(same, MOVE) == set()  # all three would land on the same name
    results = organizer.run(same, MOVE, workers=3)
    dests = [r.dest for r in results if r.outcome == "moved"]
    assert len(dests) == len(set(dests)) == 3 and all(os.path.exists(d) for d in dests)  # none overwrote another


def test_a_file_that_needs_an_answer_is_done_in_the_main_process_in_order(shared):
    import zipfile as zf
    settings, lib, rows = shared
    settings.write.conflict = "ask"
    conflicting = rows[2]  # a ComicInfo.xml that disagrees with the library: a question
    with zf.ZipFile(conflicting["path"], "a") as archive:
        archive.writestr("ComicInfo.xml", "<ComicInfo><Title>Another title</Title><Series>Elsewhere</Series></ComicInfo>")
    lib.refresh_file(conflicting["path"])
    prompter = FakePrompter()
    prompter.conflict_answer = "database"
    results = Organizer(lib, settings, prompter=prompter).run(rows, MOVE, workers=3)
    assert [r.outcome for r in results] == ["moved"] * 6
    assert [asked[0] for asked in prompter.asked if asked[0] == "conflict"] == ["conflict"]  # asked once, for that file only
    assert comicinfo_of(results[2].dest)["Title"] == "Titre 3"  # and its answer was used


def test_the_busy_callback_names_the_files_being_worked_on(shared, monkeypatch):
    import time
    from bdnex.organizer import archive as arch
    settings, lib, rows = shared
    real = arch.write_cbz
    monkeypatch.setattr(arch, "write_cbz", lambda *a, **k: (time.sleep(0.7), real(*a, **k))[1])  # slow, in every worker
    names = []
    Organizer(lib, settings, force=True).run(rows, MOVE, workers=3, on_busy=lambda n: names.append(list(n)))
    assert names and max(len(n) for n in names) == 3 and all(x.endswith(".cbz") for n in names for x in n)


def test_one_worker_or_a_preview_is_the_plain_serial_run(shared):
    settings, lib, rows = shared
    preview = Organizer(lib, settings, pretend=True).run(rows, MOVE, workers=4)
    assert [r.outcome for r in preview] == ["pretend"] * 6 and all(os.path.exists(r["path"]) for r in rows)
    stages = []
    organizer = Organizer(lib, settings, force=True)
    organizer.on_stage = lambda path, text: stages.append(text)
    organizer.run(rows[:1], MOVE, workers=1)
    assert stages[0].startswith("reading the archive") and "putting it in place" in stages and stages[-1] == "updating the library"


def test_damaged_archives_are_repaired_several_at_once(tmp_path, make_damaged_cbz):
    settings = settings_for(tmp_path, library=str(tmp_path / "rep.sqlite"))
    library = Library.for_settings(settings)
    try:
        rows = []
        for n in range(5):
            path, _ = make_damaged_cbz(f"bad{n}.cbz", pages=3, bad_page=1)
            lib_row = registered(library, path, add_series_album(library, source_id=str(n), volume=n + 1))
            assert lib_row["corrupted"] == 1
            rows.append(lib_row)
        seen = []
        results = Organizer(library, settings, force=True).run(rows, WRITE, workers=3, repair=True,
                                                               on_result=lambda n, t, r: seen.append(n))
        assert [r.outcome for r in results] == ["repaired"] * 5 and seen == [1, 2, 3, 4, 5]
        for row in rows:
            assert library.get_file(row["path"])["corrupted"] == 0  # the library knows it is clean now
            with zipfile.ZipFile(row["path"]) as archive:
                assert archive.testzip() is None
    finally:
        library.close()


def test_the_size_question_does_not_claim_drawn_pages_are_copies(capsys, monkeypatch):
    from bdnex.ui.prompts import InquirerPrompter

    prompter = InquirerPrompter()
    monkeypatch.setattr(prompter, "_select", lambda message, choices, fragments=None, shortcuts=None: "refuse")
    monkeypatch.setattr(prompter, "_need_terminal", lambda: None)
    prompter.size_action("/x/a.pdf", 10, 99, 20, drawn=371)
    assert "371 page(s) had to be drawn" in capsys.readouterr().out
    prompter.size_action("/x/a.cbr", 10, 99, 20)
    assert "byte for byte" in capsys.readouterr().out


# ---------------------------------------------------------------- write.pdf: tag (a PDF stays a PDF)
def make_pdf(path, pages=2):
    import pymupdf

    doc = pymupdf.open()
    for number in range(pages):
        doc.new_page(width=200, height=300).insert_text((20, 50), f"page {number + 1}")
    doc.save(str(path))
    doc.close()
    return str(path)


def embedded_comicinfo(path):
    import pymupdf

    with pymupdf.open(path) as doc:
        return comicinfo.parse_xml(doc.embfile_get("ComicInfo.xml")), doc.metadata, doc.get_xml_metadata()


def test_pdf_tag_writes_the_tags_into_the_pdf_and_keeps_its_bytes(lib, tmp_path):
    path = make_pdf(tmp_path / "a.pdf")
    original = open(path, "rb").read()
    row = registered(lib, path, add_series_album(lib))
    result = Organizer(lib, settings_for(tmp_path, write={"pdf": "tag"})).process(row, WRITE)
    assert result.outcome == WRITTEN and result.dest == path and not os.path.exists(str(tmp_path / "a.cbz"))
    fields, info, xmp = embedded_comicinfo(path)
    assert fields["Title"] == "Redwin de la Forge" and fields["Series"] == "Nains"
    assert info["title"] == "Nains - T1 - Redwin de la Forge" and "Jarry" in info["author"]
    assert "Redwin de la Forge" in xmp and "calibre:series" in xmp
    assert open(path, "rb").read().startswith(original)  # the pages are the original's own bytes
    again = Organizer(lib, settings_for(tmp_path, write={"pdf": "tag"})).process(lib.get_file(path), WRITE)
    assert again.outcome == UNCHANGED


def test_pdf_tag_move_puts_the_pdf_in_the_tree_with_its_extension(lib, tmp_path):
    path = make_pdf(tmp_path / "inbox" / "a.pdf") if (tmp_path / "inbox").mkdir() is None else None
    row = registered(lib, path, add_series_album(lib))
    result = Organizer(lib, settings_for(tmp_path, write={"pdf": "tag"})).process(row, MOVE)
    assert result.outcome == MOVED and result.dest.endswith(".pdf") and not os.path.exists(path)
    assert embedded_comicinfo(result.dest)[0]["Series"] == "Nains"
    assert lib.get_file(result.dest) is not None


def test_pdf_tag_preview_changes_nothing(lib, tmp_path):
    path = make_pdf(tmp_path / "a.pdf")
    before = open(path, "rb").read()
    row = registered(lib, path, add_series_album(lib))
    result = Organizer(lib, settings_for(tmp_path, write={"pdf": "tag"}), pretend=True).process(row, WRITE)
    assert result.outcome == PRETEND and "tag the PDF in place" in result.note and open(path, "rb").read() == before


def test_pdf_skip_still_leaves_the_pdf_alone_and_convert_still_converts(lib, tmp_path):
    path = make_pdf(tmp_path / "a.pdf")
    row = registered(lib, path, add_series_album(lib))
    assert Organizer(lib, settings_for(tmp_path, write={"pdf": "skip"})).process(row, WRITE).outcome == SKIPPED
    assert Organizer(lib, settings_for(tmp_path, write={"pdf": "convert", "delete_original": "no"})).process(row, WRITE).outcome == WRITTEN
    assert os.path.exists(str(tmp_path / "a.cbz"))


def test_pdf_tag_on_an_encrypted_pdf_is_moved_as_it_is_not_failed(lib, tmp_path):
    import pymupdf

    path = str(tmp_path / "secret.pdf")
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    doc.close()
    row = registered(lib, path, add_series_album(lib))
    result = Organizer(lib, settings_for(tmp_path, write={"pdf": "tag"})).process(row, WRITE)
    assert result.outcome == SKIPPED and "can not be tagged" in result.note


def test_verify_tagged_pdf_refuses_a_file_whose_start_changed(tmp_path):
    original, other = make_pdf(tmp_path / "o.pdf"), make_pdf(tmp_path / "t.pdf", pages=3)
    with pytest.raises(archive.ArchiveError):
        archive.verify_tagged_pdf(other, original, b"<ComicInfo/>")
