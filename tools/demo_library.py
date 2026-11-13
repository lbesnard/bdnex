"""A demo library with covers, a review queue and series, for looking at the web page.

Usage: uv run python tools/demo_library.py /some/scratch/folder
Then: BDNEX_CONFIG=/some/scratch/folder/bdnex.yaml uv run bdnex serve --port 8791 --allow-changes
"""
import json, os, sys, zipfile
import pymupdf, yaml
from bdnex.library import AlbumInfo, Library, SeriesInfo
from bdnex.config import load_config

root = sys.argv[1]
os.makedirs(f"{root}/comics", exist_ok=True)
conf = f"{root}/bdnex.yaml"
yaml.safe_dump({"config_version": 2, "collection_path": f"{root}/comics", "library": f"{root}/lib.sqlite",
                "cover": {"dir": f"{root}/covers"}, "import": {"sources": [{"path": f"{root}/inbox", "mode": "copy", "name": "nas"}]}},
               open(conf, "w"))
os.makedirs(f"{root}/inbox", exist_ok=True)
os.environ["BDNEX_CONFIG"] = conf
settings = load_config().settings

COLORS = [(0.84, 0.16, 0.14), (0.11, 0.31, 0.57), (0.9, 0.62, 0.15), (0.2, 0.55, 0.38), (0.45, 0.25, 0.6), (0.15, 0.15, 0.2)]

def cover_png(text, n, w=400, h=600):
    doc = pymupdf.open(); page = doc.new_page(width=w, height=h)
    page.draw_rect(page.rect, color=None, fill=COLORS[n % len(COLORS)])
    page.draw_rect(pymupdf.Rect(20, 20, w - 20, h - 20), color=(1, 1, 1), width=4)
    page.insert_textbox(pymupdf.Rect(40, 200, w - 40, 400), text, fontsize=34, color=(1, 1, 1), align=1)
    return page.get_pixmap(dpi=72).tobytes("png")

def cbz(path, text, n):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("001.png", cover_png(text, n)); z.writestr("002.png", cover_png(text, n))

series = {"Nains": 6, "Les Tuniques Bleues": 9, "Achille Talon": 7, "Lanfeust de Troy": 5}
n = 0
with Library.for_settings(settings) as lib:
    for name, count in series.items():
        info = SeriesInfo("bedetheque", name.lower().replace(" ", "-"), name)
        known = []
        for v in range(1, count + 1):
            sid = f"{abs(hash(name)) % 9999}{v}"
            title = f"{name.split()[0]} tome {v}"
            known.append({"source_id": sid, "title": title, "volume": v, "url": f"https://www.bedetheque.com/BD-{v}.html"})
            if v % 4 == 0:  # not owned
                continue
            album = lib.add_album(AlbumInfo("bedetheque", sid, title, series=info, volume=v, year=1990 + v * 3, language="fr",
                                            cover_url="u", credits={"Writer": ["Jarry, Nicolas"]}, genres=["Humour"]))
            png = cover_png(f"{name}\nT{v}", n); n += 1
            rel = f"bedetheque/{sid}/cover.png"
            os.makedirs(f"{root}/covers/bedetheque/{sid}", exist_ok=True)
            open(f"{root}/covers/{rel}", "wb").write(png)
            lib.set_cover_file(album, rel)
            path = f"{root}/comics/Series/{name}/{name} - T{v:02d}.cbz"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cbz(path, f"{name}\nT{v}", n)
            lib.add_file(path, album)
            if name == "Nains" and v == 2:
                lib.flag_review(path, "the cover differs from the album's (18% < 40%)")
        lib.conn.execute("UPDATE series SET extra = ? WHERE name = ?", (json.dumps({"albums": known}), name))
    for i in range(3):
        album = lib.add_album(AlbumInfo("bedetheque", f"9{i}", f"Un one-shot {i}", year=2015 + i, language="fr"))
        path = f"{root}/comics/OneShot/shot{i}.cbz"; os.makedirs(os.path.dirname(path), exist_ok=True); cbz(path, f"One shot {i}", i + 2)
        lib.add_file(path, album)
    lib.conn.commit()
print("ok")
