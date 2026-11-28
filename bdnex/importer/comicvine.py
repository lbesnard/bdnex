"""Comic Vine, a third place to take an album from (after bedetheque.com, the matching source, and bdtheque.com): by address.

Like bdtheque.com it is not a matching source: you give the address of a page (the review screen's ``i`` and ``a``) and the file is
linked to what it says. An *issue* page (``…/4000-123456/``) is one album; a *volume* page (``…/4050-18040/``) is a series: the marked
files get the issue with the number in their name. The API is read with `Simyan <https://github.com/Metron-Project/Simyan>`_ (an optional
install, ``pip install "bdnex[comicvine]"``), which keeps its own cache and holds Comic Vine's limits (1 request a second, 200 an hour).
Nothing here touches the library: the caller stores the ``AlbumInfo`` it gets.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Optional

from bdnex.library.models import AlbumInfo, SeriesInfo

SOURCE = "comicvine"
logger = logging.getLogger(__name__)

# https://comicvine.gamespot.com/fell/4050-18040/ (a volume) and .../fell-1-feral-city/4000-123456/ (an issue); the bare "4050-18040" too
ADDRESS = re.compile(r"(?:comicvine\.gamespot\.com/(?:[^/\s?#]+/)?|^\s*)(?P<kind>4000|4050)-(?P<id>\d+)", re.I)
KINDS = {"4000": "issue", "4050": "volume"}
KEY_ENV = "BDNEX_COMICVINE_API_KEY"
USER_AGENT = "bdnex (+https://github.com/lbesnard/bdnex)"

# Comic Vine's credit words -> ComicInfo roles
ROLES = {"writer": "Writer", "penciler": "Penciller", "penciller": "Penciller", "artist": "Penciller", "inker": "Inker", "colorist": "Colorist",
         "letterer": "Letterer", "cover": "CoverArtist", "editor": "Editor", "translator": "Translator"}


class ComicvineError(RuntimeError):
    """Said to the person as it is: no key, Simyan missing, a refused key, the hourly limit, a page that is not there."""


def parse(text: str) -> Optional[tuple[str, int]]:
    """``("issue" | "volume", id)`` for a Comic Vine address (or ``4000-N`` / ``4050-N``), else ``None``."""
    found = ADDRESS.search((text or "").strip().split("#")[0])
    return (KINDS[found.group("kind")], int(found.group("id"))) if found else None


def is_address(text: str) -> bool:
    return parse(text) is not None


def api_key(settings) -> str:
    return (os.environ.get(KEY_ENV) or settings.comicvine.api_key or "").strip()


def client(settings):
    """A Simyan client: its key, timeout and cache from the configuration, its cache and limit counters next to the library (a volume in Docker)."""
    key = api_key(settings)
    if not key:
        raise ComicvineError(f"no Comic Vine API key: set `comicvine.api_key` in your configuration (or {KEY_ENV}). "
                             "Get one at https://comicvine.gamespot.com/api/")
    try:
        from simyan.comicvine import Comicvine
    except ImportError as err:
        raise ComicvineError('Simyan is not installed: `pip install "bdnex[comicvine]"` (it needs Python 3.11 or newer)') from err
    folder = settings.library_path.parent
    folder.mkdir(parents=True, exist_ok=True)
    return Comicvine(api_key=key, timeout=settings.comicvine.timeout, cache_path=folder / "comicvine-cache.sqlite",
                     cache_expiry=timedelta(days=settings.comicvine.cache_days), ratelimit_path=folder / "comicvine-ratelimits.sqlite")


def _call(function, *args):
    """A call to Simyan, its errors said in a sentence (the key is never in a message)."""
    try:
        return function(*args)
    except ComicvineError:
        raise
    except Exception as err:
        name = type(err).__name__
        if name == "AuthenticationError":
            raise ComicvineError("Comic Vine refused the API key: check `comicvine.api_key`") from err
        if name == "RateLimitError":
            raise ComicvineError("Comic Vine's limit is reached (200 requests an hour): try again later; what was read is kept in the cache") from err
        raise ComicvineError(f"Comic Vine: {err}") from err


def _text(html: Optional[str]) -> Optional[str]:
    """A description is HTML: its text, paragraphs kept apart."""
    if not html:
        return None
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for line in soup.find_all("br"):
        line.replace_with("\n")
    for block in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "div"]):
        block.append("\n\n")  # a paragraph ends with a blank line; an inline tag (b, i, a) does not break the sentence
    text = re.sub(r"[ \t]*\n\s*\n+", "\n\n", soup.get_text("")).strip()
    return text or None


def _names(entries) -> list[str]:
    return [e.name for e in entries or [] if getattr(e, "name", None)]


def _credits(creators) -> tuple[dict[str, list[str]], list[str]]:
    credits: dict[str, list[str]] = {}
    other: list[str] = []
    for creator in creators or []:
        for word in re.split(r"[,/]", getattr(creator, "roles", "") or ""):
            word = word.strip().lower()
            role = ROLES.get(word)
            if role and creator.name and creator.name not in credits.setdefault(role, []):
                credits[role].append(creator.name)
            elif word and word not in other:
                other.append(word)
    return {role: names for role, names in credits.items() if names}, other


def series_info(volume) -> SeriesInfo:
    return SeriesInfo(SOURCE, str(volume.id), volume.name, summary=_text(getattr(volume, "summary", None) or getattr(volume, "description", None)),
                      url=str(volume.site_url) if getattr(volume, "site_url", None) else None,
                      extra={"start_year": getattr(volume, "start_year", None), "issue_count": getattr(volume, "issue_count", None)})


def album_info(issue, volume) -> AlbumInfo:
    """The library album of an issue (``issue``: Simyan's ``Issue``; ``volume``: the one it is in). A volume of one issue is a one-shot."""
    number = (issue.number or "").strip()
    several = (getattr(volume, "issue_count", None) or 2) > 1
    numeric = int(number) if number.isdigit() else None
    date = issue.cover_date or issue.store_date
    credits, other = _credits(getattr(issue, "creators", None))
    publisher = getattr(getattr(volume, "publisher", None), "name", None)
    image = getattr(issue, "image", None)
    cover = getattr(image, "original_url", None)
    extra = {"characters": _names(getattr(issue, "characters", None)), "teams": _names(getattr(issue, "teams", None)),
             "locations": _names(getattr(issue, "locations", None)), "story_arcs": _names(getattr(issue, "story_arcs", None)),
             "store_date": issue.store_date.isoformat() if issue.store_date else None, "other_roles": other}
    return AlbumInfo(
        source=SOURCE, source_id=str(issue.id), title=issue.name or (f"#{number}" if number and several else volume.name),
        series=series_info(volume) if several else None, volume=numeric if several else None,
        alternate_number=number if several and numeric is None and number else None,
        publisher=publisher, year=date.year if date else getattr(volume, "start_year", None), month=date.month if date else None,
        day=date.day if date else None, language="en", summary=_text(issue.description) or _text(getattr(issue, "summary", None)),
        cover_url=str(cover) if cover else None, url=str(issue.site_url) if getattr(issue, "site_url", None) else None,
        credits=credits, extra={key: value for key, value in extra.items() if value})


def read_issue(settings, text: str) -> AlbumInfo:
    """The album an issue address stands for (two requests at most: the issue and its volume, both cached). Slow: run it in a thread."""
    found = parse(text)
    if found is None or found[0] != "issue":
        raise ComicvineError(f"{text!r} is not the address of a Comic Vine issue (…/4000-NNNN/)")
    cv = client(settings)
    issue = _call(cv.get_issue, found[1])
    volume = _call(cv.get_volume, issue.volume.id)
    return album_info(issue, volume)


@dataclass
class VolumeAnswer:
    name: str
    mapping: dict = field(default_factory=dict)  # file path -> AlbumInfo
    unmatched: list = field(default_factory=list)  # files with no volume number, or a number the volume has no issue for
    stopped: Optional[str] = None  # why the reading ended before every file was done (the limit, the network)


def read_volume(settings, text: str, paths: list[str], progress: Optional[Callable[[str], None]] = None) -> VolumeAnswer:
    """For each file the issue of the volume with the number in its name. One request for the volume, then one per matched file (each
    kept in the cache): if the limit or the network stops it, the files done are returned with ``stopped`` saying why."""
    from bdnex.importer.folders import title_and_volume

    found = parse(text)
    if found is None or found[0] != "volume":
        raise ComicvineError(f"{text!r} is not the address of a Comic Vine volume (…/4050-NNNN/)")
    cv = client(settings)
    volume = _call(cv.get_volume, found[1])
    by_number = {}
    for entry in volume.issues:
        number = (entry.number or "").strip()
        if number.isdigit():
            by_number.setdefault(int(number), entry)
    answer = VolumeAnswer(volume.name)
    wanted = []
    for path in paths:
        number = title_and_volume(path)[1]
        if number in by_number:
            wanted.append((path, by_number[number]))
        else:
            answer.unmatched.append(path)
    for position, (path, entry) in enumerate(wanted, start=1):
        if progress:
            progress(f"{volume.name}: issue {position} of {len(wanted)}...")
        try:
            issue = _call(cv.get_issue, entry.id)
        except ComicvineError as err:
            answer.stopped = str(err)
            answer.unmatched += [p for p, _ in wanted[position - 1:]]
            break
        answer.mapping[path] = album_info(issue, volume)
    return answer


def fetch_cover(settings, info: AlbumInfo) -> Optional[str]:
    """Keep the cover of ``info`` as a file (``cover.dir``), so that the review screen's ``c`` can compare it with the file's. A plain request to
    the picture's address (not the API: it does not count against the hourly limit), checked to be a picture. Returns the relative path, or
    ``None`` (downloads are off, no cover address, or the picture did not come): a cover is a help, never a reason for a link to fail."""
    if not info.cover_url or not getattr(settings.cover, "download", False):
        return None
    from bdnex.importer import covers

    try:
        kept = covers.find_cover(settings.cover_path, SOURCE, info.source_id)
        if kept:
            return kept
        import requests

        response = requests.get(info.cover_url, headers={"User-Agent": USER_AGENT}, timeout=settings.comicvine.timeout)
        response.raise_for_status()
        covers.check_cover_image(response.content)
        return covers.save_cover(settings.cover_path, SOURCE, info.source_id, response.content)
    except Exception as err:
        logger.debug("%s: no cover (%s)", info.cover_url, err)
        return None


@dataclass
class VolumeHit:
    id: int
    name: str
    year: Optional[int]
    publisher: Optional[str]
    issues: int
    url: str


def search_volumes(settings, text: str, limit: int = 20) -> list[VolumeHit]:
    """The volumes (series) whose name looks like ``text``, best first: one request (cached). Slow: run it in a thread."""
    text = (text or "").strip()
    if not text:
        return []
    found = _call(client(settings).search_volumes, text, limit)
    return [VolumeHit(v.id, v.name, v.start_year, getattr(getattr(v, "publisher", None), "name", None), v.issue_count,
                      str(v.site_url) if getattr(v, "site_url", None) else f"https://comicvine.gamespot.com/volume/4050-{v.id}/")
            for v in found[:limit]]
