"""bdnex candidates: the other albums a doubtful file could be, with the evidence."""

import io
import json

import pytest
import yaml

from bdnex.importer import candidates, covers
from bdnex.importer.bedetheque import Bedetheque
from bdnex.library import AlbumInfo, Library
from bdnex.ui import candidates_cmd
from helpers import FakeWeb

FILE = "Nains - T01 - Redwin de la forge (Smith) [1950].cbz"


@pytest.fixture
def env(tmp_path, monkeypatch, make_cbz):
    db = tmp_path / "lib.sqlite"
    conf = tmp_path / "bdnex.yaml"
    conf.write_text(yaml.safe_dump({"config_version": 2, "library": str(db)}))
    monkeypatch.setenv("BDNEX_CONFIG", str(conf))
    path = make_cbz(FILE, pages=3)
    with Library(db) as lib:
        Bedetheque(lib, fetch=FakeWeb(), sleep=lambda s: None).update_sitemaps()
        lib.add_file(path, None)  # known to the library, no album
    return db, path


def run(*argv):
    out = io.StringIO()
    return candidates_cmd.run(list(argv), out=out), out.getvalue()


def test_a_file_without_album_gets_candidates_with_their_evidence(env):
    db, path = env
    code, text = run("--low", "--json")
    data = json.loads(text)
    assert code == 0 and [d["path"] for d in data] == [path] and data[0]["why"] == "no album"
    best = data[0]["candidates"][0]
    assert "Redwin" in best["album"] and best["score"] >= 70 and best["current"] is False
    assert best["complete"] is False  # only the sitemap is known: the page was not read


def test_low_leaves_out_the_files_that_are_fine(env):
    db, path = env
    with Library(db) as lib:
        album = lib.add_album(AlbumInfo("bedetheque", "245127", "Redwin de la Forge", volume=1, year=1950,
                                        credits={"Writer": ["Smith, John"]}, language="fr",
                                        series=None))
        lib.add_file(path, album)
    code, text = run("--low", "--json")
    assert code == 0 and json.loads(text) == []
    code, text = run("--all", "--json")  # asked for by name: listed whatever its state
    assert [d["path"] for d in json.loads(text)] == [path]


def test_text_output_marks_the_linked_album_and_changes_nothing(env):
    db, path = env
    code, text = run("--all", "--limit", "2")
    assert code == 0 and "(no album)" in text and "1 file(s)" in text
    with Library(db) as lib:
        assert lib.get_file(path)["album_id"] is None


def test_cover_marks_the_candidate_whose_cover_is_like_the_files(env, tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    import zipfile

    from importer.test_covers import art, jpeg  # noqa: F401  (the helpers of the cover tests)

    db, _ = env
    path = tmp_path / "Nains - T01 - Redwin de la forge.cbz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("page-1.jpg", jpeg(art(1)))
        archive.writestr("page-2.jpg", jpeg(art(99)))
    with Library(db) as lib:
        lib.add_file(str(path), None)
    asked = []

    def cover_image(self, url, recache=False):
        asked.append(url)
        return jpeg(art(1 if url.endswith("Couv_245127.jpg") else 2))

    monkeypatch.setattr("bdnex.importer.covers.opencv_installed", lambda: True)  # the suite switches it off
    monkeypatch.setattr(Bedetheque, "cover_image", cover_image)
    code, text = run("Redwin", "--cover", "--limit", "3", "--json")
    entry = next(d for d in json.loads(text) if d["path"] == str(path))
    best = [c for c in entry["candidates"] if c.get("cover_best")]
    assert code == 0 and len(best) == 1 and best[0]["url"].endswith("245127.html") and best[0]["cover"] >= 40
    assert all(c["cover"] is not None for c in entry["candidates"]) and len(asked) >= len(entry["candidates"])


def test_cover_uses_the_saved_cover_without_a_request(env, tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    import zipfile

    from bdnex.config import load_config
    from importer.test_covers import art, jpeg  # noqa: F401

    db, _ = env
    path = tmp_path / "Nains - T01 - Redwin de la forge.cbz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("page-1.jpg", jpeg(art(1)))
    with Library(db) as lib:
        lib.add_file(str(path), None)
    cover_dir = load_config().settings.cover_path
    for source_id, seed in (("245127", 1),):  # the import kept this one; the others are not saved
        covers.save_cover(cover_dir, "bedetheque", source_id, jpeg(art(seed)))
    asked = []
    monkeypatch.setattr("bdnex.importer.covers.opencv_installed", lambda: True)
    monkeypatch.setattr(Bedetheque, "cover_image", lambda self, url, recache=False: asked.append(url) or jpeg(art(2)))
    code, text = run("Redwin", "--cover", "--limit", "3", "--json")
    entry = next(d for d in json.loads(text) if d["path"] == str(path))
    saved = next(c for c in entry["candidates"] if c["url"].endswith("245127.html"))
    assert code == 0 and saved["cover"] >= 40 and not any(u.endswith("Couv_245127.jpg") for u in asked)


def test_each_candidate_says_where_its_score_comes_from(env):
    code, text = run("--all", "--limit", "2", "--json")
    best = json.loads(text)[0]["candidates"][0]
    assert code == 0 and best["why_score"] and best["why_score"][0].startswith("title: ")
    assert "words of the name found" in best["why_score"][0]


def test_every_step_is_said_as_it_happens_file_then_each_cover_then_the_decision(env, tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    import zipfile

    from importer.test_covers import art, jpeg

    db, _ = env
    path = tmp_path / "Nains - T01 - Redwin de la forge.cbz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("page-1.jpg", jpeg(art(1)))
    with Library(db) as lib:
        lib.add_file(str(path), None)
    monkeypatch.setattr("bdnex.importer.covers.opencv_installed", lambda: True)
    monkeypatch.setattr(Bedetheque, "cover_image",
                        lambda self, url, recache=False: jpeg(art(1 if url.endswith("Couv_245127.jpg") else 2)))
    code, text = run("Redwin", "--cover", "--limit", "3")
    lines = text.splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("[") and "Nains - T01 - Redwin de la forge.cbz" in line)
    assert "file(s) to look at (at most" in text and "/" in lines[head].split("]")[0]  # `[n/total]`: out of how many
    downloads = [i for i, line in enumerate(lines) if i > head and "downloading the cover of" in line]
    results = [i for i, line in enumerate(lines) if i > head and " cover " in line and "%" in line]
    decision = next(i for i, line in enumerate(lines) if i > head and line.strip().startswith("=>"))
    assert code == 0 and downloads and results
    assert head < downloads[0] < results[0] < decision  # the file, then each album's cover, then what was decided
    assert "no album" in text and "Couv_" in text  # which file, and which address is being fetched


def test_the_report_is_coloured_by_meaning_on_a_terminal_and_plain_otherwise():
    entry = {"score": 85, "album": "Nains Redwin", "year": 2015, "publisher": "Soleil", "complete": True, "current": True,
             "verdict": "contradicted", "cover": 3, "cover_best": False, "why_score": ["title: 85"], "for": ["year agrees"],
             "against": ["tome 5, the album is tome 1"], "url": "u"}
    plain = "\n".join(candidates_cmd.candidate_lines(1, entry, False))
    colour = "\n".join(candidates_cmd.candidate_lines(1, entry, True))
    assert "\x1b[" not in plain and "Nains Redwin" in plain and "[contradicted]" in plain
    assert "\x1b[31mcontradicted" in colour  # a contradiction is red
    assert "\x1b[32m+ year agrees" in colour and "\x1b[31m- tome 5" in colour  # evidence for is green, against is red
    assert "\x1b[1mNains Redwin" in colour  # the name of the album is what the eye lands on
    assert "\x1b[" not in "".join(candidates_cmd.head_lines(1, 9, "/a/b.cbz", None, "no album", False))


def _c(album, cover, current=False, best=False, url="https://www.bedetheque.com/BD-X-1.html"):
    return {"album": album, "cover": cover, "current": current, "cover_best": best, "url": url}


def test_the_decision_says_whether_the_best_cover_is_the_linked_album_and_what_to_type():
    path = "/comics/Alex + Ada.cbz"
    other = {"path": path, "candidates": [_c("Alex Ada T02", 14, current=True), _c("Alex Ada T01", 66, best=True, url="https://x/BD-1.html")]}
    text = "\n".join(candidates_cmd.decision_lines(other, False))
    assert "the cover of Alex Ada T01 is the file's (66%)" in text and "not the album now linked (Alex Ada T02, 14%)" in text
    assert "`bdnex import --album https://x/BD-1.html '/comics/Alex + Ada.cbz'`" in text  # quoted: the name has spaces
    same = {"path": path, "candidates": [_c("Alex Ada T02", 80, current=True, best=True)]}
    assert "the album now linked is the one whose cover is the file's (80%)" in "\n".join(candidates_cmd.decision_lines(same, False))
    none = {"path": path, "candidates": [_c("A", 4), _c("B", 6)]}
    assert "no candidate's cover looks like the file's" in "\n".join(candidates_cmd.decision_lines(none, False))
    assert "\x1b[" in "\n".join(candidates_cmd.decision_lines(other, True))


def test_a_saved_cover_is_said_too_so_no_step_is_silent(env, tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    import zipfile

    from importer.test_covers import art, jpeg

    from bdnex.config import load_config

    db, _ = env
    path = tmp_path / "Nains - T01 - Redwin de la forge.cbz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("page-1.jpg", jpeg(art(1)))
    with Library(db) as lib:
        lib.add_file(str(path), None)
    covers.save_cover(load_config().settings.cover_path, "bedetheque", "245127", jpeg(art(1)))
    monkeypatch.setattr("bdnex.importer.covers.opencv_installed", lambda: True)
    monkeypatch.setattr(Bedetheque, "cover_image", lambda self, url, recache=False: jpeg(art(2)))
    code, text = run("Redwin", "--cover", "--limit", "3")
    assert code == 0 and "using the saved cover of" in text and "downloading the cover of" in text


def test_without_a_query_or_low_it_refuses_instead_of_going_through_every_file(env, capsys):
    code, text = run("--cover")
    assert code == 2 and text == ""
    assert "every file of the library" in capsys.readouterr().err
    assert run("--low")[0] == 0 and run("--all", "--json")[0] == 0 and run("Redwin", "--json")[0] == 0


def test_a_guessed_cover_address_that_is_not_there_is_replaced_by_the_one_the_page_gives():
    from bdnex.importer.source import Candidate

    class Source:
        fetch_errors = (OSError,)

        def __init__(self):
            self.asked = []

        def cover_image(self, url):
            self.asked.append(url)
            if url.endswith("Couv_11521.jpg"):
                raise OSError("404 Client Error")
            return b"picture"

        def cover_url_of(self, candidate):
            return "https://www.bedetheque.com/media/Couvertures/NikopolInt_24012005.jpg"

    source, candidate, seen = Source(), Candidate("11521", "https://www.bedetheque.com/BD-Nikopol-INT-11521.html", "nikopol"), []
    guess = "https://www.bedetheque.com/media/Couvertures/Couv_11521.jpg"
    assert candidates.download_cover(source, candidate, guess, guessed=True, say=seen.append) == b"picture"
    assert source.asked == [guess, "https://www.bedetheque.com/media/Couvertures/NikopolInt_24012005.jpg"] and seen == [source.asked[1]]
    with pytest.raises(OSError):  # an address the library knows is not guessed: its 404 stands (no page is read)
        candidates.download_cover(Source(), candidate, guess, guessed=False)
