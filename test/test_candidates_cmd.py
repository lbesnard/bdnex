"""bdnex candidates: the other albums a doubtful file could be, with the evidence."""

import io
import json

import pytest
import yaml

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
    code, text = run("--json")  # asked for by name: listed whatever its state
    assert [d["path"] for d in json.loads(text)] == [path]


def test_text_output_marks_the_linked_album_and_changes_nothing(env):
    db, path = env
    code, text = run("--limit", "2")
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
