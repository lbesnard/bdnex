"""Send the library's metadata to a Komga server (``bdnex komga``).

Komga reads ``ComicInfo.xml`` from CBZ and CBR files, but not from a PDF: for a PDF it only reads the pages. Its API
can set the metadata of a book and of a series, so the library (which knows everything about each album) is pushed
there. A book of Komga is matched to a file of the library by its file name and size, so the two machines do not need
the same folders. Nothing here reads or changes a comic file.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Iterable, Optional

import requests

PAGE_SIZE = 500
ROLES = {"Writer": "writer", "Penciller": "penciller", "Inker": "inker", "Colorist": "colorist",
         "Letterer": "letterer", "CoverArtist": "cover", "Editor": "editor", "Translator": "translator",
         "Other": "other"}
BOOK_LOCKS = {"title": "titleLock", "summary": "summaryLock", "number": "numberLock", "numberSort": "numberSortLock",
              "releaseDate": "releaseDateLock", "authors": "authorsLock", "isbn": "isbnLock", "links": "linksLock",
              "tags": "tagsLock"}
SERIES_LOCKS = {"title": "titleLock", "publisher": "publisherLock", "language": "languageLock", "genres": "genresLock",
                "summary": "summaryLock", "status": "statusLock", "totalBookCount": "totalBookCountLock",
                "links": "linksLock"}


class KomgaError(Exception):
    """Komga can not be reached, or refused: the message says what to check."""


class Client:
    """The few calls of the Komga API that are needed. ``session`` is a ``requests.Session`` (a test passes its own)."""

    def __init__(self, config, session: Optional[requests.Session] = None):
        if not config.url:
            raise KomgaError("komga.url is not set: put the address of your Komga server in the configuration "
                             "(`bdnex config --edit`), e.g. http://localhost:25600")
        self.base = config.url.rstrip("/")
        self.timeout = config.timeout
        self.session = session or requests.Session()
        if config.api_key:
            self.session.headers["X-API-Key"] = config.api_key
        elif config.username and config.password:
            self.session.auth = (config.username, config.password)
        else:
            raise KomgaError("no Komga login: set komga.api_key (Komga: account settings, API keys) or "
                             "komga.username and komga.password")

    def _call(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self.session.request(method, f"{self.base}{path}", timeout=self.timeout, **kwargs)
        except requests.RequestException as err:
            raise KomgaError(f"can not reach Komga at {self.base}: {err}") from err
        if response.status_code in (401, 403):
            raise KomgaError(f"Komga refused the login ({response.status_code}): check komga.api_key or the "
                             "username and password, and that the account may edit metadata")
        if response.status_code >= 400:
            raise KomgaError(f"Komga answered {response.status_code} to {method} {path}: {response.text[:200]}")
        return response.json() if response.content else None

    def books(self) -> Iterable[dict[str, Any]]:
        """Every book. ``POST /books/list`` is the current call; ``GET /books`` (deprecated since Komga 1.19) is the
        fallback for a server that refuses an empty search."""
        try:
            first = self._call("POST", "/api/v1/books/list", params={"page": 0, "size": PAGE_SIZE}, json={})
            fetch = lambda page: self._call("POST", "/api/v1/books/list",  # noqa: E731
                                            params={"page": page, "size": PAGE_SIZE}, json={})
        except KomgaError as err:
            if "refused the login" in str(err) or "can not reach" in str(err):
                raise
            first = self._call("GET", "/api/v1/books", params={"page": 0, "size": PAGE_SIZE})
            fetch = lambda page: self._call("GET", "/api/v1/books",  # noqa: E731
                                            params={"page": page, "size": PAGE_SIZE})
        page, data = 0, first
        while True:
            yield from data["content"]
            page += 1
            if data.get("last", True) or not data["content"]:
                return
            data = fetch(page)

    def series(self, series_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/v1/series/{series_id}")

    def patch_book(self, book_id: str, payload: dict[str, Any]) -> None:
        self._call("PATCH", f"/api/v1/books/{book_id}/metadata", json=payload)

    def patch_series(self, series_id: str, payload: dict[str, Any]) -> None:
        self._call("PATCH", f"/api/v1/series/{series_id}/metadata", json=payload)


# --------------------------------------------------------------------------------------------------- matching
def _common_tail(a: str, b: str) -> int:
    """How many trailing folders and the name two paths share (more = more likely the same file)."""
    left, right = a.lower().split("/")[::-1], b.lower().split("/")[::-1]
    count = 0
    for x, y in zip(left, right):
        if x != y:
            break
        count += 1
    return count


def match(files: list[dict[str, Any]], books: list[dict[str, Any]]) -> dict[str, list]:
    """Pair library files with Komga books: ``{"pairs": [(file, book, how)], "no_book": [file], "ambiguous": [file]}``.

    First by file name and size; then by file name alone when that name is unique on both sides (a file that changed
    since Komga scanned it has another size). When several books have the same name and size, the one whose folders
    end like the file's wins; a tie is left alone, never guessed.
    """
    by_key: dict[tuple, list] = {}
    by_name: dict[str, list] = {}
    for book in books:
        name = os.path.basename(book["url"]).lower()
        by_key.setdefault((name, book.get("sizeBytes")), []).append(book)
        by_name.setdefault(name, []).append(book)
    names = Counter(os.path.basename(f["path"]).lower() for f in files)
    pairs, no_book, ambiguous, taken = [], [], [], set()
    for file in files:
        name = os.path.basename(file["path"]).lower()
        found, how = by_key.get((name, file.get("size")), []), "name and size"
        if not found and names[name] == 1 and len(by_name.get(name, [])) == 1:
            found, how = by_name[name], "name"
        if not found:
            no_book.append(file)
            continue
        if len(found) > 1:
            ranked = sorted(found, key=lambda b: _common_tail(file["path"], b["url"]), reverse=True)
            if _common_tail(file["path"], ranked[0]["url"]) == _common_tail(file["path"], ranked[1]["url"]):
                ambiguous.append(file)
                continue
            found = ranked[:1]
        book = found[0]
        if book["id"] in taken:  # two library files for one Komga book: do not let them fight over it
            ambiguous.append(file)
            continue
        taken.add(book["id"])
        pairs.append((file, book, how))
    return {"pairs": pairs, "no_book": no_book, "ambiguous": ambiguous}


# --------------------------------------------------------------------------------------------------- payloads
def release_date(album: dict[str, Any]) -> Optional[str]:
    if not album.get("year"):
        return None
    return f"{int(album['year']):04d}-{int(album.get('month') or 1):02d}-{int(album.get('day') or 1):02d}"


def authors_of(album: dict[str, Any]) -> list[dict[str, str]]:
    return [{"name": name, "role": ROLES[role]} for role in ROLES for name in album["credits"].get(role, [])]


def book_fields(album: dict[str, Any], current: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """What the library knows of an album, in the fields of a Komga book (only those it knows).

    The genres become the book's tags, added to the tags Komga already has (yours are never removed)."""
    fields: dict[str, Any] = {"title": album["title"]}
    if album.get("summary"):
        fields["summary"] = album["summary"]
    if album.get("volume") is not None:
        fields["number"] = str(album["volume"])
        fields["numberSort"] = float(album["volume"])
    if release_date(album):
        fields["releaseDate"] = release_date(album)
    if authors_of(album):
        fields["authors"] = authors_of(album)
    if album.get("isbn"):
        fields["isbn"] = album["isbn"]
    if album.get("url"):
        fields["links"] = [{"label": "Bédéthèque", "url": album["url"]}]
    if album["genres"]:
        tags = {t.lower(): t for t in album["genres"]}
        tags.update({t.lower(): t for t in (current or {}).get("tags") or []})  # Komga's own spelling wins
        fields["tags"] = sorted(tags.values(), key=str.lower)
    return fields


STATUSES = (("ONGOING", ("en cours", "continu")), ("ENDED", ("termin", "fini", "complet")),
            ("ABANDONED", ("abandon", "interrompu", "arrete")), ("HIATUS", ("suspendu", "pause", "hiatus")))


def status_of(text: Optional[str]) -> Optional[str]:
    """Komga's series status from Bédéthèque's words (``Terminée``, ``En cours``...); ``None`` when not recognised."""
    import unicodedata
    folded = "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))
    return next((status for status, words in STATUSES if any(w in folded for w in words)), None)


def series_fields(albums: list[dict[str, Any]]) -> dict[str, Any]:
    """The fields of a Komga series from the albums of the library that are in it (names: the commonest wins).

    The series' own record (``album["series_record"]``, read by the command) adds its summary, page link, status and
    the number of numbered albums the source lists."""
    names = Counter((a["series"] or a["title"]) for a in albums)
    fields: dict[str, Any] = {"title": names.most_common(1)[0][0]}
    for key, name in (("publisher", "publisher"), ("language", "language")):
        values = Counter(a[key] for a in albums if a.get(key))
        if values:
            fields[name] = values.most_common(1)[0][0]
    genres = sorted({g for a in albums for g in a["genres"]})
    if genres:
        fields["genres"] = genres
    record = next((a["series_record"] for a in albums if a.get("series_record")), None)
    if record:
        if record.get("summary"):
            fields["summary"] = record["summary"]
        if record.get("url"):
            fields["links"] = [{"label": "Bédéthèque", "url": record["url"]}]
        extra = record.get("extra") or {}
        if status_of(extra.get("status")):
            fields["status"] = status_of(extra["status"])
        numbered = [a for a in extra.get("albums") or [] if a.get("volume") is not None]
        if numbered:  # integrals and special issues have no number: they are not counted
            fields["totalBookCount"] = len(numbered)
    return fields


def _same(key: str, mine: Any, theirs: Any) -> bool:
    if key == "authors":
        return {(a["name"], a["role"].lower()) for a in mine} == {(a["name"], a["role"].lower()) for a in theirs or []}
    if key == "links":
        return {(x["label"], x["url"]) for x in mine} == {(x.get("label"), x.get("url")) for x in theirs or []}
    if key in ("genres", "tags"):
        return {g.lower() for g in mine} == {g.lower() for g in theirs or []}
    if key == "totalBookCount":
        return theirs is not None and int(mine) == int(theirs)
    if key == "numberSort":
        return theirs is not None and abs(float(mine) - float(theirs)) < 1e-9
    return (mine or "") == (theirs or "")


def changes(fields: dict[str, Any], current: dict[str, Any], locks: dict[str, str],
            lock: bool) -> tuple[dict[str, Any], list[str]]:
    """``(payload, differing)``: what to send, and which fields really differ from what Komga holds.

    Nothing differing and nothing to lock sends nothing, so a second run is a no-op. A field that is the same but not
    locked yet is still sent when ``lock`` is on, so the lock is set once.
    """
    payload, differing = {}, []
    for key, value in fields.items():
        different = not _same(key, value, current.get(key))
        unlocked = lock and key in locks and not current.get(locks[key])
        if different:
            differing.append(key)
        if different or unlocked:
            payload[key] = value
            if lock and key in locks:
                payload[locks[key]] = True
    return payload, differing
