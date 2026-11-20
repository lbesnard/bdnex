"""The flags the old cover threshold (40) left are taken back once, and only those."""
from bdnex.library import AlbumInfo, Library

KEY = "cleanup-cover-flags-40"


def test_a_flag_from_the_old_threshold_goes_once_and_the_others_stay(tmp_path, capsys, make_cbz):
    path = tmp_path / "lib.sqlite"
    with Library(path) as lib:
        album = lib.add_album(AlbumInfo("bedetheque", "1", "Redwin"))
        for name, reason, score in [("old-ok", "the cover differs from the album's (12% < 40%)", 12.0),  # now fine: cover 12 >= 3
                                    ("old-bad", "the cover differs from the album's (2% < 40%)", 2.0),  # still doubtful
                                    ("own-threshold", "the cover differs from the album's (30% < 50%)", 30.0),  # another threshold: not ours
                                    ("other-reason", "applied at 85%: nothing confirms it", 60.0)]:
            file = make_cbz(f"{name}.cbz")
            lib.add_file(file, album)
            lib.flag_review(file, reason)
            lib.set_file_fields(file, cover_score=score)
        lib.delete_meta(KEY)  # as in a library from before this change
    capsys.readouterr()
    with Library(path) as lib:
        flags = {name: lib.get_file(str(tmp_path / f"{name}.cbz")).get("review") for name in ("old-ok", "old-bad", "own-threshold", "other-reason")}
        assert flags["old-ok"] is None  # taken back
        assert flags["old-bad"] and flags["own-threshold"] and flags["other-reason"]  # everything else stays
        assert lib.get_meta(KEY) == "done"
    assert "1 file(s) were flagged because their cover scored below 40" in capsys.readouterr().err
    with Library(path) as lib:  # once: a flag written later, even with the same words, is not touched
        lib.flag_review(str(tmp_path / "old-ok.cbz"), "the cover differs from the album's (12% < 40%)")
    with Library(path) as lib:
        assert lib.get_file(str(tmp_path / "old-ok.cbz")).get("review")
    assert "taken back" not in capsys.readouterr().err


def test_a_new_library_says_nothing_and_never_cleans_up_later(tmp_path, capsys):
    with Library(tmp_path / "new.sqlite") as lib:
        assert lib.get_meta(KEY) == "done"
    assert capsys.readouterr().err == ""
