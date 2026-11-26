"""The picture of `bdnex review` for the README (PNG, drawn by Textual from a demo library of made-up files, never a real library).

Usage: uv run python tools/readme_screen.py docs/_static/screens/review-screen.png
Needs rsvg-convert (librsvg) to turn Textual's SVG into a PNG. Run it again after the screen changes, and commit the picture.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import zipfile

import pymupdf
import yaml

from bdnex.importer.source import Candidate

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "docs/_static/screens/review-screen.png")
ROOT = "/tmp/bdnex-demo"  # a fixed name: it shows in the picture
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(f"{ROOT}/comics", exist_ok=True)
conf = f"{ROOT}/bdnex.yaml"
yaml.safe_dump({"config_version": 2, "library": f"{ROOT}/lib.sqlite", "collection_path": f"{ROOT}/comics", "cover": {"dir": f"{ROOT}/covers"}},
               open(conf, "w"))
os.environ["BDNEX_CONFIG"] = conf
os.environ["HOME"] = ROOT  # nothing of the real configuration or library is read

from bdnex.config import load_config  # noqa: E402
from bdnex.importer import covers  # noqa: E402
from bdnex.library import AlbumInfo, Library, SeriesInfo  # noqa: E402
from bdnex.library.review_batch import CHECKED  # noqa: E402
from bdnex.ui import review_tui  # noqa: E402

COLORS = [(0.84, 0.16, 0.14), (0.11, 0.31, 0.57), (0.9, 0.62, 0.15), (0.2, 0.55, 0.38), (0.45, 0.25, 0.6)]


def picture(text, n, w=400, h=600):
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    page.draw_rect(page.rect, color=None, fill=COLORS[n % len(COLORS)])
    page.draw_circle((w / 2, 230), 90, color=None, fill=(1, 0.95, 0.3))
    page.insert_textbox(pymupdf.Rect(30, 380, w - 30, 520), text, fontsize=34, color=(1, 1, 1), align=1)
    return page.get_pixmap(dpi=72).tobytes("png")


# (folder, series, volume, title, year, publisher, writer, artist, score, state)  state: confirmed | auto | flagged | none
ROWS = [
    ("Series/Nains", "Nains", 1, "Redwin de la Forge", 2007, "Soleil", "Istin, Jean-Luc", "Jaouen, Olivier", 96, "confirmed"),
    ("Series/Nains", "Nains", 2, "Forge", 2008, "Soleil", "Istin, Jean-Luc", "Jaouen, Olivier", 82, "flagged"),
    ("Series/Nains", "Nains", 3, "Magie", 2009, "Soleil", "Istin, Jean-Luc", "Jaouen, Olivier", 93, "auto"),
    ("Series/Nains", "Nains", 4, "Les Nains de Ténèbres", 2010, "Soleil", "Istin, Jean-Luc", "Jaouen, Olivier", 85, "medium"),
    ("Series/Astérix", "Astérix", 15, "La Zizanie", 1970, "Dargaud", "Goscinny, René", "Uderzo, Albert", 97, "confirmed"),
    ("Series/Astérix", "Astérix", 19, "Le Devin", 1972, "Dargaud", "Goscinny, René", "Uderzo, Albert", 94, "auto"),
    ("Series/Blake et Mortimer", "Blake et Mortimer", 1, "Le Secret de l'Espadon", 1950, "Blake et Mortimer", "Jacobs, Edgar P.", "Jacobs, Edgar P.", 91, "auto"),
    ("Series/Blake et Mortimer", "Blake et Mortimer", 2, "Le Mystère de la Grande Pyramide", 1954, "Blake et Mortimer", "Jacobs, Edgar P.", "Jacobs, Edgar P.", 88, "medium"),
    ("Series/Tintin", "Tintin", 5, "Le Lotus bleu", 1936, "Casterman", "Hergé", "Hergé", 98, "confirmed"),
    ("Series/Tintin", "Tintin", 6, "L'Oreille cassée", 1937, "Casterman", "Hergé", "Hergé", 95, "confirmed"),
    ("OneShot", None, None, "Le Chat du Rabbin", 2002, "Dargaud", "Sfar, Joann", "Sfar, Joann", 92, "auto"),
    ("OneShot", None, None, "Persepolis", 2000, "L'Association", "Satrapi, Marjane", "Satrapi, Marjane", 70, "flagged"),
    ("Manga/Akira", "Akira", 1, "Akira", 1990, "Glénat", "Otomo, Katsuhiro", "Otomo, Katsuhiro", 97, "confirmed"),
    ("Manga/Akira", "Akira", 2, "Akira", 1991, "Glénat", "Otomo, Katsuhiro", "Otomo, Katsuhiro", 93, "auto"),
]

settings = load_config().settings
with Library.for_settings(settings) as library:
    for number, (folder, series, volume, title, year, publisher, writer, artist, score, state) in enumerate(ROWS):
        os.makedirs(f"{ROOT}/comics/{folder}", exist_ok=True)
        name = f"{series} - T{volume:02d} - {title}.cbz" if series else f"{title}.cbz"
        path = f"{ROOT}/comics/{folder}/{name}"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("001.png", picture(title, number))
        source_id = 1000 + number
        album = library.add_album(AlbumInfo(
            "bedetheque", str(source_id), title, series=SeriesInfo("bedetheque", series.lower(), series) if series else None, volume=volume,
            year=year, pages=48, publisher=publisher, language="fr", genres=["Aventure", "Humour"] if number % 2 else ["Fantastique"],
            credits={"Writer": [writer], "Penciller": [artist]},
            url=f"https://www.bedetheque.com/BD-{(series or title).replace(' ', '-')}-{source_id}.html"))
        relative = covers.save_cover(settings.cover_path, "bedetheque", str(source_id), picture(title, number))
        library.set_cover_file(album, relative)
        library.add_file(path, album)
        library.log_import(path, "tagged", float(score), album)
        if state == "flagged":
            library.flag_review(path, f"applied at {score}%: nothing confirms it")
        if state == "confirmed":
            library.set_attr("file", library.get_file(path)["id"], CHECKED, "keep")
    for name in ("Spirou - T12 - Inconnu.cbz", "Largo Winch 3.cbz"):
        loose = f"{ROOT}/comics/Series/{name}"
        with zipfile.ZipFile(loose, "w") as z:
            z.writestr("001.png", picture(name, 4))
        library.add_file(loose, None)
        library.log_import(loose, "skipped", 55.0)


class Source:
    name = "bedetheque"

    def catalogue(self):
        return []

    def candidates(self, text, limit=5):
        return []

    def candidate_for(self, text):
        return None


async def main():
    with Library.for_settings(settings) as library:
        app = review_tui.ReviewApp(library, settings, Source(), 50, (), False, everything=True, tree=True)
        async with app.run_test(size=(210, 38)) as pilot:
            for _ in range(60):
                await pilot.pause(0.2)
                if app.folder_tree is not None and app.move_tree is not None and app.rows:
                    break
            app.open_folder("Series")
            for _ in range(30):
                await pilot.pause(0.2)
            flagged = next((i for i, row in enumerate(app.rows) if row.why and row.album), 0)
            app.query_one("DataTable").move_cursor(row=flagged)  # a flagged one: its evidence shows
            for node in app.folder_tree.root.children:
                if node.data is not None and node.data.name == "Series":
                    node.expand()
            await pilot.pause(0.5)
            svg = OUT[:-4] + ".svg"
            with open(svg, "w", encoding="utf-8") as handle:
                handle.write(app.export_screenshot(title="bdnex review"))
    subprocess.run(["rsvg-convert", "-w", "1800", svg, "-o", OUT], check=True)
    os.remove(svg)
    print(f"wrote {OUT}")


asyncio.run(main())
