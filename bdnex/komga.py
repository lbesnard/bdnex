"""Send the library's metadata to a Komga server (``bdnex komga``).

Komga reads ``ComicInfo.xml`` from CBZ and CBR files, but not from a PDF: for a PDF it only reads the pages. Its API
can set the metadata of a book and of a series, so the library (which knows everything about each album) is pushed
there. A book of Komga is matched to a file of the library by its file name and size, so the two machines do not need
the same folders. Nothing here reads or changes a comic file.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Iterable, Optional

import requests

PAGE_SIZE = 500
ROLES = {"Writer": "writer", "Penciller": "penciller", "Inker": "inker", "Colorist": "colorist",
         "Letterer": "letterer", "CoverArtist": "cover", "Editor": "editor", "Translator": "translator",
         "Other": "other", "Storyboard": "storyboard", "Décors": "background", "Design": "design",
         "Préface": "foreword", "Adapté de": "adapted from"}
BOOK_LOCKS = {"title": "titleLock", "summary": "summaryLock", "number": "numberLock", "numberSort": "numberSortLock",
              "releaseDate": "releaseDateLock", "authors": "authorsLock", "isbn": "isbnLock", "links": "linksLock",
              "tags": "tagsLock"}
SERIES_LOCKS = {"title": "titleLock", "publisher": "publisherLock", "language": "languageLock", "genres": "genresLock",
                "summary": "summaryLock", "status": "statusLock", "totalBookCount": "totalBookCountLock",
                "links": "linksLock"}


class KomgaError(Exception):
    """Komga can not be reached, or refused: the message says what to check. ``status``: the HTTP status, when there was one."""

    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body  # what Komga answered, whole (the message holds only its start)


class Client:
    """The few calls of the Komga API that are needed. ``session`` is a ``requests.Session`` (a test passes its own)."""

    def __init__(self, config, session: Optional[requests.Session] = None):
        if not config.url:
            raise KomgaError("komga.url is not set: put the address of your Komga server in the configuration "
                             "(`bdnex config --edit`), e.g. http://localhost:25600")
        if not config.url.lower().startswith(("http://", "https://")):
            raise KomgaError(f"komga.url must start with http:// or https:// (it is {config.url!r})")
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
        try:  # never follow a redirect: the key would go with it (to another host), and a PATCH answered by a 301 could be replayed as a GET
            response = self.session.request(method, f"{self.base}{path}", timeout=self.timeout, allow_redirects=False, **kwargs)
        except requests.RequestException as err:
            raise KomgaError(f"can not reach Komga at {self.base}: {err}") from err
        if 300 <= response.status_code < 400:
            raise KomgaError(f"Komga redirected {method} {path} to {response.headers.get('Location', 'another address')}: set komga.url to "
                             "that address (bdnex does not send your login along a redirect)", response.status_code)
        if response.status_code in (401, 403):
            raise KomgaError(f"Komga refused the login ({response.status_code}): check komga.api_key or the "
                             "username and password, and that the account may edit metadata", response.status_code)
        if response.status_code >= 400:
            raise KomgaError(f"Komga answered {response.status_code} to {method} {path}: {response.text[:200]}", response.status_code, response.text)
        return response.json() if response.content else None

    def books(self) -> Iterable[dict[str, Any]]:
        """Every book. ``POST /books/list`` is the current call; ``GET /books`` (deprecated since Komga 1.19) is the
        fallback for a server that refuses an empty search."""
        try:
            first = self._call("POST", "/api/v1/books/list", params={"page": 0, "size": PAGE_SIZE}, json={})
            fetch = lambda page: self._call("POST", "/api/v1/books/list",  # noqa: E731
                                            params={"page": page, "size": PAGE_SIZE}, json={})
        except KomgaError as err:
            if err.status not in (400, 404, 405, 415, 501):  # only "this server does not take that call": not a login, a network or a passing 500
                raise
            first = self._call("GET", "/api/v1/books", params={"page": 0, "size": PAGE_SIZE})
            fetch = lambda page: self._call("GET", "/api/v1/books",  # noqa: E731
                                            params={"page": page, "size": PAGE_SIZE})
        page, data, seen = 0, first, set()
        while True:
            for book in data["content"]:
                if book.get("id") not in seen:  # a book added or removed during the listing shifts the pages: never twice
                    seen.add(book.get("id"))
                    yield book
            page += 1
            last = data.get("last")
            if last is None:  # no `last` flag: the page count says it (never "stop after the first page" on a missing key)
                last = page >= int(data.get("totalPages") or 1)
            if last or not data["content"]:
                return
            data = fetch(page)

    def series(self, series_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/v1/series/{series_id}")

    def patch_book(self, book_id: str, payload: dict[str, Any]) -> list[str]:
        """Send the metadata of one book. Komga checks a few fields (an ISBN must be a valid one) and refuses the WHOLE request for one bad
        value: the fields it names are taken out and the rest is sent again, once. Returns the fields that were left out."""
        try:
            self._call("PATCH", f"/api/v1/books/{book_id}/metadata", json=payload)
            return []
        except KomgaError as err:
            bad = refused_fields(err, payload)
            if not bad:
                raise
            rest = {k: v for k, v in payload.items() if k not in bad and k not in {BOOK_LOCKS.get(b) for b in bad}}
            if not any(k in rest for k in BOOK_LOCKS):  # nothing but the refused values was left to send
                return bad
            self._call("PATCH", f"/api/v1/books/{book_id}/metadata", json=rest)
            return bad

    def patch_series(self, series_id: str, payload: dict[str, Any]) -> None:
        self._call("PATCH", f"/api/v1/series/{series_id}/metadata", json=payload)


def refused_fields(err: "KomgaError", payload: dict[str, Any]) -> list[str]:
    """The fields of ``payload`` that Komga named in a 400 (``{"violations": [{"fieldName": "isbn", ...}]}``)."""
    if err.status != 400:
        return []
    try:
        violations = json.loads(err.body).get("violations") or []
    except (ValueError, AttributeError):
        return []
    return sorted({v.get("fieldName") for v in violations if isinstance(v, dict) and v.get("fieldName") in payload})


def valid_isbn(text) -> Optional[str]:
    """The ISBN as Komga takes it (13 digits, no hyphens), or ``None`` when it is not a valid one (a wrong check digit, a wrong length): Komga
    refuses a whole update for one bad ISBN. An ISBN-10 is turned into its ISBN-13."""
    digits = "".join(ch for ch in str(text or "").upper() if ch.isdigit() or ch == "X")
    if len(digits) == 10 and (digits[:9].isdigit()) and (digits[9].isdigit() or digits[9] == "X"):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(digits))
        if total % 11:
            return None
        digits = "978" + digits[:9]
        digits += str((10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits)) % 10) % 10)
        return digits
    if len(digits) == 13 and digits.isdigit():
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits))
        return digits if total % 10 == 0 else None
    return None


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


def _name(path: str) -> str:
    """A file name to compare: Unicode-composed (a name typed on one system and one scanned on another differ only in how `é` is written)
    and lower case."""
    import unicodedata

    return unicodedata.normalize("NFC", os.path.basename(path)).lower()


def match(files: list[dict[str, Any]], books: list[dict[str, Any]]) -> dict[str, list]:
    """Pair library files with Komga books: ``{"pairs": [(file, book, how)], "no_book": [file], "ambiguous": [file]}``.

    First by file name and size; then by file name alone when that name is unique on both sides AND the folder it is in
    has the same name (a file that changed since Komga scanned it has another size, but `01.cbz` of one series is not
    `01.cbz` of another). When several books have the same name and size, the one whose folders
    end like the file's wins; a tie is left alone, never guessed.
    """
    by_key: dict[tuple, list] = {}
    by_name: dict[str, list] = {}
    for book in books:
        name = _name(book["url"])
        by_key.setdefault((name, book.get("sizeBytes")), []).append(book)
        by_name.setdefault(name, []).append(book)
    names = Counter(_name(f["path"]) for f in files)
    pairs, no_book, ambiguous, taken = [], [], [], set()
    for file in files:
        name = _name(file["path"])
        found, how = by_key.get((name, file.get("size")), []), "name and size"
        if not found and names[name] == 1 and len(by_name.get(name, [])) == 1 and _common_tail(file["path"], by_name[name][0]["url"]) >= 2:
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


def link_label(url: str) -> str:
    """The name of the site a page address belongs to, for a Komga link."""
    return "BDTheque" if "bdtheque.com" in (url or "") else "Bédéthèque"


def book_fields(album: dict[str, Any], current: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """What the library knows of an album, in the fields of a Komga book (only those it knows).

    The genres become the book's tags, added to the tags Komga already has (yours are never removed), and the authors keep the roles
    Komga holds that the library has no names for."""
    fields: dict[str, Any] = {"title": album["title"]}
    if album.get("summary"):
        fields["summary"] = album["summary"]
    if album.get("volume") is not None:
        volume = float(album["volume"])
        fields["number"] = str(int(volume)) if volume.is_integer() else str(volume)  # `1`, not `1.0`: Komga's own number is `1`
        fields["numberSort"] = volume
    if release_date(album):
        fields["releaseDate"] = release_date(album)
    if authors_of(album):
        mine = authors_of(album)
        said = {a["role"].lower() for a in mine}
        # the roles the library has names for are replaced; the other roles Komga holds (a colorist someone added there) are kept
        fields["authors"] = mine + [a for a in (current or {}).get("authors") or [] if str(a.get("role", "")).lower() not in said]
    if valid_isbn(album.get("isbn")):  # an invalid one would make Komga refuse the whole update
        fields["isbn"] = valid_isbn(album["isbn"])
    if album.get("url"):
        fields["links"] = [{"label": link_label(album["url"]), "url": album["url"]}]
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
    import re

    folded = "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))
    if re.search(r"\b(?:non|pas)\s+(?:termin|fini|complet)", folded):  # `Non terminée` is not "ended"
        return "ONGOING"
    if "discontinu" in folded or re.search(r"\bnon\s+continu", folded):  # nor is `discontinuée` "ongoing"
        return "ABANDONED"
    return next((status for status, words in STATUSES if any(w in folded for w in words)), None)


def series_fields(albums: list[dict[str, Any]], current: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The fields of a Komga series from the albums of the library that are in it (names: the commonest wins). The genres are added
    to the genres Komga already has (Komga's spelling wins), as the tags of a book are: yours are never removed.

    The series' own record (``album["series_record"]``, read by the command) adds its summary, page link, status and
    the number of numbered albums the source lists."""
    names = Counter((a["series"] or a["title"]) for a in albums)
    fields: dict[str, Any] = {"title": names.most_common(1)[0][0]}
    for key, name in (("publisher", "publisher"), ("language", "language")):
        values = Counter(a[key] for a in albums if a.get(key))
        if values:
            fields[name] = values.most_common(1)[0][0]
    genres = {g.lower(): g for a in albums for g in a["genres"]}
    if genres:
        genres.update({g.lower(): g for g in (current or {}).get("genres") or []})
        fields["genres"] = sorted(genres.values(), key=str.lower)
    record = next((a["series_record"] for a in albums if a.get("series_record")), None)
    if record:
        if record.get("summary"):
            fields["summary"] = record["summary"]
        if record.get("url"):
            fields["links"] = [{"label": link_label(record["url"]), "url": record["url"]}]
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
            lock: bool, respect_locks: bool = True, locked_out: Optional[list[str]] = None) -> tuple[dict[str, Any], list[str]]:
    """``(payload, differing)``: what to send, and which fields really differ from what Komga holds.

    Nothing differing and nothing to lock sends nothing, so a second run is a no-op. A field that is the same but not
    locked yet is still sent when ``lock`` is on, so the lock is set once.

    ``respect_locks``: a field that differs and is **already locked in Komga** is left alone (someone locked it there on purpose, or an earlier
    push did and it was changed since): it is added to ``locked_out`` instead of being sent. ``False`` overwrites it (``--overwrite-locked``).
    """
    payload, differing = {}, []
    for key, value in fields.items():
        different = not _same(key, value, current.get(key))
        if different and respect_locks and key in locks and current.get(locks[key]):
            if locked_out is not None:
                locked_out.append(key)
            continue
        unlocked = lock and key in locks and not current.get(locks[key])
        if different:
            differing.append(key)
        if different or unlocked:
            payload[key] = value
            if lock and key in locks:
                payload[locks[key]] = True
    return payload, differing
