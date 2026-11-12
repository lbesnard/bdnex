

def test_tome_zero_without_a_title_is_a_one_shot():
    from bdnex.importer.bedetheque.parser import partial_album_info
    from bdnex.importer.source import Candidate
    info = partial_album_info(Candidate("1", "https://www.bedetheque.com/BD-13m28-Tome-0-112782.html", "13m28", 0))
    assert info.series is None and info.volume is None and info.title == "13m28"
    named = partial_album_info(Candidate("2", "https://www.bedetheque.com/BD-Nains-Tome-0-Prelude-9.html", "x", 0))
    assert named.volume == 0
