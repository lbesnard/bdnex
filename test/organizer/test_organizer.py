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


def settings_for(tmp_path, **overrides):
    user = {"config_version": 2, "directory": str(tmp_path / "library"), **overrides}
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
    assert forced.asked == [] and comicinfo_of(path)["Title"] == "Redwin de la Forge"


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
    if answer == "keep_both":
        assert second.dest.endswith("(2).cbz") and os.path.exists(first.dest)
    if answer == "replace":
        assert second.dest == first.dest and lib.get_file(first.dest)["id"] == b["id"]
        assert lib.stats()["files"] == 1  # the replaced file's row is gone, not duplicated


def test_force_numbers_clashes_instead_of_asking(lib, make_cbz, tmp_path):
    org = Organizer(lib, settings_for(tmp_path), FakePrompter(), force=True)
    org.process(registered(lib, make_cbz("a.cbz", pages=3), add_series_album(lib)), MOVE)
    result = org.process(registered(lib, make_cbz("b.cbz", pages=5), add_series_album(lib, source_id="2")), MOVE)
    assert result.dest.endswith("(2).cbz") and org.prompter.asked == []


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
    assert results[0].outcome == FAILED and "directory" in results[0].note


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
