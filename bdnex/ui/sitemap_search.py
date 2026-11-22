"""``bdnex ls --sitemap``: look through the whole album list (the sitemap) by words, or for the albums a file could be (what ``fzf`` does in the review screen)."""

from __future__ import annotations

import json
import sys

from bdnex.importer.registry import UnknownSource, create_source
from bdnex.library import Library
from bdnex.importer.registry import offline_settings


def build(settings, library):
    """The album list of the source, loaded (no request: the list is already stored). Raises ``NotReady``."""
    from bdnex.importer.album_search import AlbumList

    offline = offline_settings(settings)
    source = create_source(settings.source, library, offline)
    if not source.is_ready():
        raise LookupError("The album list is not downloaded yet: run `bdnex init` first.")
    warm = getattr(source, "warm", None)
    if warm:
        warm()
    return AlbumList(source)


def entry(candidate, score: float, found_by=(), parts=()) -> dict:
    from bdnex.ui.prompts import display_name

    return {"score": round(float(score), 1), "name": display_name(candidate), "volume": candidate.volume, "url": candidate.url,
            "found_by": list(found_by), "evidence": list(parts)}


def run(settings, words: list[str], files: list[str], limit: int, as_json: bool = False, count: bool = False, out=sys.stdout) -> int:
    """The albums of the whole list that look like ``words``, and for each of ``files`` the albums it could be."""
    from bdnex.importer.album_search import search_file
    from bdnex.ui import color

    on = color.enabled(out)
    report = []
    with Library.for_settings(settings, readonly=True) as library:
        try:
            albums = build(settings, library)
        except (LookupError, UnknownSource) as err:
            print(err, file=sys.stderr)
            return 1
        if words:
            query = " ".join(words)
            hits, total = albums.search(query, limit=limit)
            series, _ = albums.search_series(query, limit=limit)
            report.append({"query": query, "matches": total, "albums": [entry(c, s) for c, s in hits],
                           "series": [{"score": round(float(s), 1), "name": c.name, "url": c.url} for c, s in series]})
        for path in files:
            hits = search_file(albums, path, limit=limit)
            report.append({"file": path, "albums": [entry(h.candidate, h.score, h.found_by, h.parts) for h in hits]})
    if count:
        print(sum(len(block["albums"]) for block in report), file=out)
        return 0
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=out)
        return 0
    for block in report:
        title = block.get("file") or block["query"]
        total = f" ({block['matches']} in the list)" if "matches" in block else ""
        print(color.role(title, "name", on) + color.role(total, "note", on), file=out)
        for item in block.get("series", [])[:5]:
            print(f"      {color.role('series', 'note', on)} {color.paint_score(format(item['score'], '5.1f'), item['score'], on)}  {item['name']}"
                  f"\n        {color.role(item['url'], 'path', on)}", file=out)
        if not block["albums"]:
            print("    " + color.role("nothing in the album list", "warn", on), file=out)
        for number, album in enumerate(block["albums"], start=1):
            volume = f"T{album['volume']}" if album["volume"] is not None else "  -"
            score = color.paint_score(format(album["score"], "5.1f"), album["score"], on)
            print(f"  {number:>2}. {score}  {volume:<5} {album['name']}\n        {color.role(album['url'], 'path', on)}", file=out)
            for note in album["evidence"]:
                print(f"        {color.role(note, 'note', on)}", file=out)
    return 0
