"""An untrusted archive is read whole into memory and rewritten: limits on what it may take, and no Zip Slip names in what is written."""

import zipfile

import pytest

from bdnex.organizer import archive


def zip_of(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return str(path)


def test_a_member_bigger_than_the_limit_is_refused_before_it_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "MAX_MEMBER", 1000)
    path = zip_of(tmp_path / "big.cbz", [("p1.jpg", b"x" * 5000)])
    with pytest.raises(archive.ArchiveError, match="in memory"):
        archive.read_entries(path, "cbz", "auto", 150)


def test_a_member_that_packs_like_a_bomb_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "BIG_MEMBER", 1000)
    monkeypatch.setattr(archive, "MAX_RATIO", 20)
    path = zip_of(tmp_path / "bomb.cbz", [("p1.jpg", b"\0" * 200_000)])  # packs about 1000 to 1
    with pytest.raises(archive.ArchiveError, match="not a picture"):
        archive.read_entries(path, "cbz", "auto", 150)


def test_the_total_and_the_number_of_members_are_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "MAX_TOTAL", 3000)
    path = zip_of(tmp_path / "total.cbz", [(f"p{n}.jpg", bytes([n]) * 1500) for n in range(3)])
    with pytest.raises(archive.ArchiveError, match="more than"):
        archive.read_entries(path, "cbz", "auto", 150)
    monkeypatch.undo()
    monkeypatch.setattr(archive, "MAX_MEMBERS", 2)
    with pytest.raises(archive.ArchiveError, match="members"):
        archive.read_entries(path, "cbz", "auto", 150)


def test_the_lenient_reader_has_the_same_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "MAX_MEMBER", 1000)
    path = zip_of(tmp_path / "big.cbz", [("p1.jpg", b"x" * 5000)])
    with pytest.raises(archive.ArchiveError):
        archive.read_entries_lenient(path, "cbz")


def test_names_with_dots_slashes_or_a_drive_are_cleaned_and_never_twice_the_same(tmp_path):
    path = zip_of(tmp_path / "evil.cbz", [("../../evil.jpg", b"a"), ("/abs/p.png", b"b"), ("C:/win.jpg", b"c"), ("dir\\\\x.jpg", b"d"),
                                           ("evil.jpg", b"e"), ("ok/01.jpg", b"f")])
    entries, _ = archive.read_entries(path, "cbz", "auto", 150)
    names = [entry.name for entry in entries]
    assert len(set(names)) == len(names) == 6
    assert all(not name.startswith("/") and ".." not in name.split("/") and ":" not in name for name in names), names
    assert "ok/01.jpg" in names and "abs/p.png" in names and "evil.jpg" in names  # a normal name stays as it is
    rewritten = tmp_path / "new.cbz"
    archive.write_cbz(str(rewritten), entries, None)
    with zipfile.ZipFile(rewritten) as zf:
        assert not [n for n in zf.namelist() if n.startswith("/") or ".." in n.split("/")]  # nothing a later unzip could follow out of its folder
    archive.verify_cbz(str(rewritten), entries, None)  # and the check still agrees with what was written
