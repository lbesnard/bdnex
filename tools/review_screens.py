"""Pictures of `bdnex review --batch` for the documentation (SVG, drawn by Textual from a demo library of made-up files).

Usage: uv run python tools/review_screens.py docs/_static/screens
Run it again after the screen changes, and commit the pictures.
"""
import asyncio
import os
import sys
import shutil
import zipfile

import pymupdf
import yaml

from bdnex.importer.source import Candidate

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "docs/_static/screens")
ROOT = "/tmp/bdnex-demo"  # a fixed name: it shows in the pictures
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
os.makedirs(OUT, exist_ok=True)
os.makedirs(f"{ROOT}/comics/Series/Nains", exist_ok=True)
conf = f"{ROOT}/bdnex.yaml"
yaml.safe_dump({"config_version": 2, "library": f"{ROOT}/lib.sqlite", "cover": {"dir": f"{ROOT}/covers"}}, open(conf, "w"))
os.environ["BDNEX_CONFIG"] = conf
os.environ["HOME"] = ROOT  # nothing of the real configuration or library is read

from bdnex.config import load_config  # noqa: E402
from bdnex.importer import covers  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402
from bdnex.ui import review_tui  # noqa: E402

COLORS = [(0.84, 0.16, 0.14), (0.11, 0.31, 0.57), (0.9, 0.62, 0.15), (0.2, 0.55, 0.38), (0.45, 0.25, 0.6)]


def picture(text, n, w=400, h=600):
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    page.draw_rect(page.rect, color=None, fill=COLORS[n % len(COLORS)])
    page.draw_circle((w / 2, 230), 90, color=None, fill=(1, 0.95, 0.3))
    page.insert_textbox(pymupdf.Rect(30, 380, w - 30, 520), text, fontsize=34, color=(1, 1, 1), align=1)
    return page.get_pixmap(dpi=72).tobytes("png")


# (file, folder, series, volume, title, album id, score, cover score, flagged)
ROWS = [
    ("Nains - T02 - Forge.cbz", "Series/Nains", "Nains", 2, "Forge", 245128, 82, 4, True),
    ("Nains T03.cbz", "Series/Nains", "Nains", 3, "Magie", 245129, 85, 48, False),
    ("Nains - 04 - Les Nains de Ténèbres.cbz", "Series/Nains", "Nains", 4, "Ténèbres", 245130, 88, 51, False),
    ("Redwin de la Forge.cbz", "Series/Nains", "Nains", 1, "Redwin de la Forge", 245127, 74, 12, True),
    ("Astérix - La Zizanie.cbz", "Series/Astérix", "Astérix", 15, "La Zizanie", 1001, 90, 62, False),
    ("Asterix Le Devin.cbz", "Series/Astérix", "Astérix", 19, "Le Devin", 1002, 86, 47, False),
]

settings = load_config().settings
with Library(f"{ROOT}/lib.sqlite") as library:
    for number, (name, folder, series, volume, title, source_id, score, cover, flagged) in enumerate(ROWS):
        os.makedirs(f"{ROOT}/comics/{folder}", exist_ok=True)
        path = f"{ROOT}/comics/{folder}/{name}"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("001.png", picture(f"{series} {volume}", number))
            z.writestr("002.png", picture("page 2", number + 1))
        album = library.add_album(AlbumInfo(
            "bedetheque", str(source_id), title, series=SeriesInfo("bedetheque", series.lower(), series), volume=volume,
            year=2010 + number, pages=48, credits={"Writer": ["Istin, Jean-Luc"], "Penciller": ["Jaouen, Olivier"]},
            url=f"https://www.bedetheque.com/BD-{series}-Tome-{volume}-{title.replace(' ', '-')}-{source_id}.html"))
        relative = covers.save_cover(settings.cover_path, "bedetheque", str(source_id), picture(f"{series} {volume}", number + (0 if cover > 30 else 2)))
        library.set_cover_file(album, relative)
        library.add_file(path, album)
        library.log_import(path, "tagged", float(score), album)
        library.set_file_fields(path, cover_score=float(cover))
        if flagged:
            library.flag_review(path, f"applied at {score}%: nothing confirms it")
    loose = f"{ROOT}/comics/Series/Nains/Nains - T09 - Inconnu.cbz"
    with zipfile.ZipFile(loose, "w") as z:
        z.writestr("001.png", picture("Nains 9", 4))
    library.add_file(loose, None)
    library.log_import(loose, "skipped", 58.0)


class Source:
    name = "bedetheque"

    def __init__(self):
        names = [("Nains", 1, "Redwin de la Forge", 245127), ("Nains", 2, "Forge", 245128), ("Nains", 3, "Magie", 245129),
                 ("Nains", 9, "Le Pas du Nain", 245135), ("Astérix", 15, "La Zizanie", 1001), ("Astérix", 19, "Le Devin", 1002)]
        self.list = [Candidate(str(i), f"https://www.bedetheque.com/BD-{s}-Tome-{v}-{t.replace(' ', '-')}-{i}.html", f"{s} tome {v} {t}".lower(), v)
                     for s, v, t, i in names]

    def catalogue(self):
        return self.list

    def candidates(self, text, limit=5):
        return [(c, 80.0) for c in self.list[:limit]]

    def candidate_for(self, text):
        return None


def screenshot(app, name):
    with open(f"{OUT}/{name}.svg", "w", encoding="utf-8") as handle:
        handle.write(app.export_screenshot(title="bdnex review --batch 50 --unmatched"))
    print(f"wrote {OUT}/{name}.svg")


async def main():
    with Library(f"{ROOT}/lib.sqlite") as library:
        app = review_tui.ReviewApp(library, settings, Source(), 50, (), True)
        async with app.run_test(size=(172, 32)) as pilot:
            for _ in range(5):
                await app.workers.wait_for_complete()
                await pilot.pause()
            await pilot.press("k")  # one decision, so that the State column shows more than one word
            await pilot.pause()
            screenshot(app, "review-table")
            await pilot.press("o")
            for _ in range(5):
                await app.workers.wait_for_complete()
                await pilot.pause()
            for letter in "nains":
                await pilot.press(letter)
            for _ in range(5):
                await app.workers.wait_for_complete()
                await pilot.pause(0.2)
            screenshot(app, "review-search")
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("c")
            for _ in range(20):
                await pilot.pause(0.2)
            screenshot(app, "review-covers")


asyncio.run(main())
