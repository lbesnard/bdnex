"""The FastAPI application.

Reading: the database is opened read-only for every request. Changing (jobs, review answers) is off unless
``api.allow_changes`` is true, and then needs the login whenever one is set (the token as a bearer header, or the
user name and password as HTTP Basic, which a browser asks for and then remembers), a header a web page of another site
cannot send, and a request from the page's own address.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from bdnex.api import auth, settings_api
from bdnex.api.history import History
from bdnex.api.watch import Watcher
from bdnex.api.jobs import SPECS, Busy, JobError, JobManager
from bdnex.config import Settings, user_config_path
from bdnex.library import Library
from bdnex.library.query import QueryError

STATIC = Path(__file__).resolve().parent / "static"
MAX_LIMIT = 1000
MAX_BULK = 2000  # albums one edit may change
MAX_BODY = 1_000_000  # bytes of a request body: the page sends a few lines of JSON
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}  # what `Host` says when the server is reached on this machine


class JobRequest(BaseModel):
    command: str
    preview: bool = True        # a preview changes nothing: the real run has to be asked for
    query: list[str] = []
    paths: list[str] = []
    copy_files: bool = False    # `move` leaves the originals where they are
    batch: Optional[str] = None
    interactive: bool = False   # a real run asks its questions on the page (else it never asks)
    steps: list[str] = []       # import: match and / or fetch
    auto: str = ""              # import: how much to ask (`strong`, `all`)
    files: str = ""             # import: `database_only` (touch no file) or `no_move`
    force: bool = False         # import: redo the steps for files that are done (rematch a file)
    formats: list[str] = []     # convert: `pdf` and / or `cbr`
    older_than: Optional[int] = None  # trash-empty: only what was trashed that many days ago
    id: Optional[int] = None    # trash-restore: the trash entry
    place: bool = False         # trash-restore: then move it where its album belongs
    old: Optional[str] = None   # relocate: the folder the files used to be in
    new: Optional[str] = None   # relocate: the folder they are in now
    album: Optional[str] = None  # apply-album: the album's number at the source, or its page address
    only: Optional[str] = None   # komga-push / komga-show: `books` or `series`
    fields: list[str] = []       # komga-push / komga-show: only these fields
    with_review: bool = False    # komga-push: also the files whose match waits for a review


class SettingsChange(BaseModel):
    changes: dict[str, Any]     # {"write.pdf": "tag", "import.workers": 2}
    preview: bool = True


class MarksChange(BaseModel):
    q: str
    set: dict[str, str] = {}
    remove: list[str] = []
    albums: bool = False       # mark the albums the query finds, not the files
    preview: bool = False


class FileSpare(BaseModel):
    spare: bool = True


class FileTrash(BaseModel):
    reason: str = "moved to the trash from the web page"


class Login(BaseModel):
    username: str
    password: str


class QuestionAnswer(BaseModel):
    question: int               # the id of the question being answered
    choice: Optional[int] = None
    choices: list[int] = []
    text: str = ""


class AlbumEdit(BaseModel):
    preview: bool = True        # a preview shows the changes and stores nothing
    fields: dict[str, Any]      # the album's fields as `bdnex edit` shows them: title, volume, credits {role: [names]}, ...


class BulkEdit(BaseModel):
    q: str                      # which albums, as in `bdnex ls`
    set: list[str]              # `publisher=Soleil`, `genres=Humour; Fantasy`, `credits.Writer=A; B` (as `bdnex edit --set`)
    preview: bool = True


class ReviewAnswer(BaseModel):
    action: str                 # keep | unlink | rematch | album
    album: Optional[str] = None  # `album`: the album's number at the source, or its page address
    interactive: bool = False    # `rematch`: let the task ask which album, on the page


def create_app(settings: Settings, token: Optional[str] = None, allow_changes: Optional[bool] = None,
               password: Optional[str] = None, username: Optional[str] = None):
    """The application for ``settings``; ``token`` (default ``api.token``) is required when set; ``allow_changes``
    (default ``api.allow_changes``) lets the page run jobs and answer reviews. ``password`` (default ``api.password``)
    with ``username`` (default ``api.username``) is a login for browsers: HTTP Basic."""
    try:
        from importlib.metadata import version
        __version__ = version("bdnex")
    except Exception:
        __version__ = "unknown"

    token = settings.api.token if token is None else token
    allow_changes = settings.api.allow_changes if allow_changes is None else allow_changes
    password = settings.api.password if password is None else password
    username = settings.api.username if username is None else username
    jobs = JobManager(settings)
    history = History(Path(str(settings.library_path) + ".changes.jsonl"))
    watcher = Watcher(settings, jobs)
    app = FastAPI(
        title="bdnex", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,  # served below, behind the login
        description="Read-only view of your bdnex library. The `q` parameter takes the same terms as "
                    "`bdnex ls` (`series:nains year:2010..2015 ^genre:humour`).")
    app.router.on_startup.append(watcher.start)  # looks at the import sources when `watch.enabled` (not for a test client)
    app.router.on_shutdown.append(watcher.stop)

    throttle, sessions = auth.Throttle(), auth.Sessions(settings.api.session_hours)

    def client(request: Request) -> str:
        """Who is asking, for the limit on wrong guesses. Behind a proxy that is the LAST address it forwarded (`api.behind_proxy`): a
        proxy appends the address it saw, so the first entries are whatever the client chose to send (a guess counted against a new
        address each time would never lock anybody); else every visitor would look like the proxy and one bad guesser would lock everybody out."""
        if settings.api.behind_proxy:
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
            if forwarded:
                return forwarded
        return request.client.host if request.client else "?"

    loopback_server = settings.api.host in ("127.0.0.1", "localhost", "::1")
    host_names = {name.lower() for name in settings.api.allowed_hosts} | (LOOPBACK_NAMES if loopback_server else set())

    def host_ok(request: Request) -> bool:
        """DNS rebinding: a page of another site whose name points at this machine sends `Host: its-name` and passes every same-origin
        check. A server listening only on this machine answers to its own names (and `api.allowed_hosts`); one reachable from elsewhere
        answers to any name unless `api.allowed_hosts` lists them."""
        if not host_names:
            return True
        host = request.headers.get("host", "").strip().lower()
        name = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return name in host_names

    def secure(request: Request) -> bool:
        scheme = request.headers.get("x-forwarded-proto", "") if settings.api.behind_proxy else ""
        return (scheme or request.url.scheme) == "https"

    def refuse(request: Request, tried: bool) -> HTTPException:
        """401 for a request without a valid login. The page's own requests get no Basic challenge (the browser's login
        box would come up over the page's login form); other clients (curl) do."""
        headers = {"X-Bdnex-Login": "password" if password else "token"}
        if not request.headers.get("x-bdnex-page"):
            headers["WWW-Authenticate"] = 'Basic realm="bdnex", charset="UTF-8"' if password else "Bearer"
        return HTTPException(401, "a login is required" if password else "a valid `Authorization: Bearer <token>` header is required",
                             headers=headers)

    def allowed(request: Request) -> None:
        if not token and not password:
            return
        who = client(request)
        wait = throttle.wait(who)
        if wait:
            raise HTTPException(429, f"too many wrong guesses: try again in {wait} s", headers={"Retry-After": str(wait)})
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        scheme = scheme.lower()
        if token and scheme == "bearer" and auth.same(value, token):
            return
        if password and scheme == "basic":
            name, secret = auth.basic_credentials(value)
            if auth.login_ok(name, secret, username, password):
                return
        if password and sessions.valid(request.cookies.get(auth.COOKIE)):
            return
        if scheme in ("basic", "bearer"):  # a login was offered and is wrong
            throttle.failed(who)
        raise refuse(request, bool(scheme))

    def can_change(request: Request) -> None:
        """A change needs: changes enabled, the token (if any), our own header, and no foreign origin."""
        allowed(request)
        request.state.change = True  # `protect` records it in the history once it went through
        if not allow_changes:
            raise HTTPException(403, "changes are disabled: set `api.allow_changes: true` in the configuration "
                                     "(or start the server with --allow-changes)")
        if request.headers.get("x-requested-with") != "bdnex":
            raise HTTPException(403, "missing the `X-Requested-With: bdnex` header")
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            raise HTTPException(403, "this request comes from another site")

    def library():
        if not settings.library_path.exists():
            raise HTTPException(503, f"no library yet ({settings.library_path}): run `bdnex import` first")
        try:
            lib = Library.for_settings(settings, readonly=True)
        except Exception as err:
            raise HTTPException(503, str(err)) from err
        try:
            yield lib
        finally:
            lib.close()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")  # the page's own files: nothing of the library
    guarded = [Depends(allowed)]
    changing = [Depends(can_change)]

    def terms(q: str) -> list[str]:
        try:
            return shlex.split(q)
        except ValueError as err:
            raise HTTPException(400, f"bad query: {err}") from err

    def page(rows: list[dict[str, Any]], limit: int, offset: int) -> dict[str, Any]:
        return {"count": len(rows), "offset": offset, "limit": limit, "items": rows[offset:offset + limit]}

    @app.get("/healthz", include_in_schema=False)
    def healthz():  # for a container's health check: no login, nothing of the library
        return {"status": "ok"}

    @app.get("/openapi.json", include_in_schema=False, dependencies=guarded)
    def openapi_json():  # the whole schema, every writing route included: behind the login like the rest
        return app.openapi()

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False, dependencies=guarded)
    def swagger_page():
        from fastapi.openapi.docs import get_swagger_ui_html

        return get_swagger_ui_html(openapi_url="/openapi.json", title="bdnex API")

    @app.get("/redoc", response_class=HTMLResponse, include_in_schema=False, dependencies=guarded)
    def redoc_page():
        from fastapi.openapi.docs import get_redoc_html

        return get_redoc_html(openapi_url="/openapi.json", title="bdnex API")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        """The page. With a password and no valid login a browser gets the login form (status 401, so nothing caches it
        as the page); other clients get the plain 401 and its challenge."""
        if password:
            try:
                allowed(request)
            except HTTPException as err:
                if err.status_code == 401 and "text/html" in request.headers.get("accept", ""):
                    return HTMLResponse((STATIC / "login.html").read_text(encoding="utf-8"), status_code=401)
                raise
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.post("/api/login", include_in_schema=False)
    def login(body: Login, request: Request, response: Response):
        if not password:
            raise HTTPException(404, "this server has no password")
        who = client(request)
        wait = throttle.wait(who)
        if wait:
            raise HTTPException(429, f"too many wrong guesses: try again in {wait} s", headers={"Retry-After": str(wait)})
        if request.headers.get("x-requested-with") != "bdnex":
            raise HTTPException(403, "missing the `X-Requested-With: bdnex` header")
        if not auth.login_ok(body.username, body.password, username, password):
            throttle.failed(who)
            raise HTTPException(401, "wrong user name or password")
        throttle.succeeded(who)
        response.set_cookie(auth.COOKIE, sessions.start(), max_age=int(sessions.ttl), httponly=True, samesite="strict",
                            secure=secure(request), path="/")
        return {"ok": True}

    @app.post("/api/logout", include_in_schema=False)
    def logout(request: Request, response: Response):
        if request.headers.get("x-requested-with") != "bdnex":
            raise HTTPException(403, "missing the `X-Requested-With: bdnex` header")
        sessions.end(request.cookies.get(auth.COOKIE))
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.middleware("http")
    async def protect(request: Request, call_next):
        """Small bodies only, and headers that keep the page from being framed, sniffed or fed from elsewhere."""
        if not host_ok(request):
            return Response("this name is not one the server answers to (`api.allowed_hosts`)", status_code=400)
        length = request.headers.get("content-length") or "0"
        if not length.isdigit():
            return Response("a bad Content-Length", status_code=400)
        if int(length) > MAX_BODY:
            return Response("the request is too big", status_code=413)
        if "chunked" in request.headers.get("transfer-encoding", "").lower():  # no length to check: the body could be any size
            return Response("send the body with a Content-Length", status_code=411)
        response = await call_next(request)
        if getattr(request.state, "change", False) and response.status_code < 400:
            who = (username if password else "token" if token else "local") + "@" + client(request)
            history.record(who, request.method, request.url.path, response.status_code, getattr(request.state, "detail", None))
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):  # the API docs page uses a CDN
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get("/api/history", dependencies=guarded, summary="What the page and the API changed: who, when, what (newest first)")
    def history_view(limit: int = Query(100, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0)):
        total, items = history.recent(limit, offset)
        return {"count": total, "offset": offset, "limit": limit, "items": items}

    @app.get("/api/health", dependencies=guarded, summary="Is the server up, and which library does it read?")
    def health(lib: Library = Depends(library)):
        return {"status": "ok", "version": __version__, "schema": lib.schema_version, "token_required": bool(token), "login_required": bool(password)}

    @app.get("/api/stats", dependencies=guarded, summary="What the library holds, as `bdnex stats --json`")
    def stats(q: str = Query("", description="only these albums and files (`bdnex ls` terms)"), lib: Library = Depends(library)):
        from bdnex.ui import stats_cmd

        try:
            return stats_cmd.summary(lib, terms(q))
        except QueryError as err:
            raise HTTPException(400, f"bad query: {err}") from err

    @app.get("/api/fields", dependencies=guarded, summary="The fields a query can use, with their kind and help")
    def fields():
        from bdnex.ui import stats_cmd

        return {"items": stats_cmd.field_list()}

    @app.get("/api/albums", dependencies=guarded, summary="Albums matching a query")
    def albums(q: str = Query("", description="`bdnex ls` terms"), limit: int = Query(100, ge=1, le=MAX_LIMIT),
               offset: int = Query(0, ge=0), lib: Library = Depends(library)):
        try:
            total, rows = lib.page(terms(q), limit=limit, offset=offset)
        except QueryError as err:
            raise HTTPException(400, f"bad query: {err}") from err
        return {"count": total, "offset": offset, "limit": limit, "items": rows}

    @app.get("/api/albums/{album_id}", dependencies=guarded, summary="One album, with its files")
    def album(album_id: int, lib: Library = Depends(library)):
        found = lib.get_album(album_id)
        if found is None:
            raise HTTPException(404, f"no album {album_id}")
        found["files"] = lib.files_for_album(album_id)
        found["marks"] = lib.attrs_of("album", album_id)
        for file in found["files"]:
            file["marks"] = lib.attrs_of("file", file["id"])
        return found

    def apply_edits(lib_edit: Library, changes: dict[int, dict[str, Any]]) -> None:
        for album_id, change in changes.items():
            lib_edit.edit_album(album_id, change)

    @app.post("/api/albums/{album_id}/edit", dependencies=guarded,
              summary="**Change (unless a preview):** edit an album's metadata by hand, as `bdnex edit` does")
    def album_edit(album_id: int, body: AlbumEdit, request: Request):
        from bdnex.ui import edit_cmd

        if not body.preview:
            can_change(request)
            if jobs.busy():
                raise HTTPException(409, "a task is running: wait for it to finish, or cancel it")
        lib = Library.for_settings(settings, readonly=body.preview)
        try:
            album = lib.get_album(album_id)
            if album is None:
                raise HTTPException(404, f"no album {album_id}")
            problems: list[str] = []
            change = edit_cmd.doc_changes(album, dict(body.fields), problems)
            if problems:
                raise HTTPException(400, "; ".join(problems))
            lines = edit_cmd.describe_changes({album_id: album}, {album_id: change}) if change else []
            if change and not body.preview:
                apply_edits(lib, {album_id: change})
            return {"changed": bool(change), "stored": bool(change) and not body.preview, "changes": lines,
                    "album": lib.get_album(album_id) if not body.preview else album}
        except (ValueError, KeyError) as err:
            raise HTTPException(400, str(err)) from err
        finally:
            lib.close()

    @app.post("/api/edit", dependencies=guarded,
              summary="**Change (unless a preview):** one change on every album a query finds, as `bdnex edit --set`")
    def bulk_edit(body: BulkEdit, request: Request):
        from bdnex.ui import edit_cmd

        if not body.q.strip():
            raise HTTPException(400, "give a query: this changes every album it finds")
        if not body.set:
            raise HTTPException(400, "nothing to set")
        if not body.preview:
            can_change(request)
            if jobs.busy():
                raise HTTPException(409, "a task is running: wait for it to finish, or cancel it")
        try:
            assignments = edit_cmd.parse_assignments(body.set)
        except ValueError as err:
            raise HTTPException(400, f"bad value: {err}") from err
        lib = Library.for_settings(settings, readonly=body.preview)
        try:
            try:
                rows = [r for r in lib.query(terms(body.q)) if r.get("id")]
            except QueryError as err:
                raise HTTPException(400, f"bad query: {err}") from err
            if len(rows) > MAX_BULK:
                raise HTTPException(400, f"{len(rows)} albums: narrow the query (at most {MAX_BULK} at once)")
            albums = {r["id"]: lib.get_album(r["id"]) for r in rows}
            changes, problems, skipped = {}, [], 0
            for album in albums.values():
                if "series" in assignments and album["kind"] != "series":
                    skipped += 1
                    continue
                change = edit_cmd.doc_changes(album, dict(assignments), problems)
                if change:
                    changes[album["id"]] = change
            if problems:
                raise HTTPException(400, "; ".join(problems[:5]))
            if not body.preview:
                apply_edits(lib, changes)
            return {"matched": len(albums), "changed": len(changes), "skipped": skipped, "stored": not body.preview and bool(changes),
                    "changes": edit_cmd.describe_changes(albums, changes)[:400]}
        except (ValueError, KeyError) as err:
            raise HTTPException(400, str(err)) from err
        finally:
            lib.close()

    @app.get("/api/albums/{album_id}/cover", dependencies=guarded, summary="The album's cover image")
    def cover(album_id: int, lib: Library = Depends(library)):
        found = lib.get_album(album_id)
        if found is None:
            raise HTTPException(404, f"no album {album_id}")
        kept = found.get("cover_file")
        target = (settings.cover_path / kept) if kept else None
        # the stored name is relative to cover.dir; never serve anything that resolves outside of it
        if target is None or not target.is_file() or settings.cover_path.resolve() not in target.resolve().parents:
            raise HTTPException(404, "this album has no saved cover (`bdnex import --fetch` downloads it)")
        return FileResponse(target, headers={"Cache-Control": "private, max-age=600"})

    def file_change(request: Request, file_id: int, act):
        """One change on one file of the library, under the same rules as every other change."""
        can_change(request)
        if jobs.busy():
            raise HTTPException(409, "a task is running: wait for it to finish, or cancel it")
        lib = Library.for_settings(settings)
        try:
            row = lib.file_by_id(file_id)
            if row is None:
                raise HTTPException(404, f"no file {file_id}")
            return act(lib, row)
        finally:
            lib.close()

    @app.post("/api/files/{file_id}/spare", dependencies=guarded, summary="**Change:** mark a file as a spare version of its album (or not)")
    def file_spare(file_id: int, body: FileSpare, request: Request):
        def act(lib, row):
            lib.set_file_fields(row["path"], spare=int(body.spare))
            return {"spare": body.spare, "note": "`move` puts it in the spare versions folder" if body.spare else "`move` puts it with its album"}
        return file_change(request, file_id, act)

    @app.post("/api/files/{file_id}/unlink", dependencies=guarded, summary="**Change:** forget which album a file belongs to")
    def file_unlink(file_id: int, request: Request):
        def act(lib, row):
            lib.set_file_album(row["path"], None)
            return {"unlinked": True, "note": "`bdnex import` matches it again whenever you like"}
        return file_change(request, file_id, act)

    @app.post("/api/files/{file_id}/trash", dependencies=guarded, summary="**Change:** move a file to the trash (it can be restored)")
    def file_trash(file_id: int, body: FileTrash, request: Request):
        from bdnex import trash

        def act(lib, row):
            source = settings.source_for(row["path"])
            if source is not None and source.mode == "copy":
                raise HTTPException(409, "this file is under a source that is only copied from: it is never changed")
            if not os.path.exists(row["path"]):
                raise HTTPException(409, "this file is not on the disk (any more)")
            try:
                entry = trash.move_to_trash(lib, settings, row["path"], body.reason[:200] or "moved to the trash from the web page", row=row)
            except OSError as err:
                raise HTTPException(500, f"could not move it: {err}") from err
            return {"trashed": True, "trash_id": entry}
        return file_change(request, file_id, act)

    @app.get("/api/files", dependencies=guarded, summary="Files matching a query")
    def files(q: str = Query("", description="`bdnex ls --files` terms"),
              limit: int = Query(100, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0),
              lib: Library = Depends(library)):
        try:
            total, rows = lib.page(terms(q), files=True, limit=limit, offset=offset)
        except QueryError as err:
            raise HTTPException(400, f"bad query: {err}") from err
        return {"count": total, "offset": offset, "limit": limit, "items": rows}

    @app.get("/api/series", dependencies=guarded, summary="Series, what you own and what is missing")
    def series(name: str = Query("", description="words of the series name"), missing: bool = False,
               lib: Library = Depends(library)):
        rows = lib.series_overview(name.split())
        if missing:
            rows = [r for r in rows if r["missing"]]
        return {"count": len(rows), "items": rows}

    @app.get("/api/people", dependencies=guarded, summary="Authors: each credited name once, with the albums they have by role")
    def people(q: str = Query("", description="part of a name"), limit: int = Query(100, ge=1, le=MAX_LIMIT),
               offset: int = Query(0, ge=0), lib: Library = Depends(library)):
        return {**lib.people_summary(q, limit, offset), "offset": offset, "limit": limit}

    @app.get("/api/series/{series_id}", dependencies=guarded, summary="One series: each volume, owned or missing")
    def series_detail(series_id: int, lib: Library = Depends(library)):
        row = lib.conn.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no series {series_id}")
        import json as _json

        known = _json.loads(row["extra"]).get("albums")
        mine = {r["source_id"]: dict(r) for r in lib.conn.execute(
            "SELECT a.id, a.source_id, a.title, a.volume, a.cover_file, a.url, "
            "(SELECT COUNT(*) FROM files f WHERE f.album_id = a.id) AS files FROM albums a WHERE a.series_id = ?", (series_id,))}
        volumes = []
        for entry in known if known is not None else []:
            have = mine.pop(str(entry["source_id"]), None)
            volumes.append({"volume": entry.get("volume"), "title": entry.get("title"), "url": entry.get("url"),
                            "album_id": have["id"] if have else None, "owned": bool(have and have["files"]),
                            "has_cover": bool(have and have["cover_file"])})
        for have in mine.values():  # albums of the series the source's list does not show (or no list at all)
            volumes.append({"volume": have["volume"], "title": have["title"], "url": have["url"], "album_id": have["id"],
                            "owned": bool(have["files"]), "has_cover": bool(have["cover_file"])})
        volumes.sort(key=lambda v: (v["volume"] is None, v["volume"] or 0, v["title"] or ""))
        return {"id": row["id"], "name": row["name"], "url": row["url"], "list_known": known is not None, "volumes": volumes}

    @app.get("/api/trash", dependencies=guarded, summary="Files waiting in the trash")
    def trash(lib: Library = Depends(library)):
        rows = lib.trash_entries()
        return {"count": len(rows), "items": rows}

    # ------------------------------------------------------------------ more to look at
    @app.get("/api/overview", dependencies=guarded, summary="Counts and the state of the library, for the front page")
    def overview(lib: Library = Depends(library)):
        count = lambda sql: lib.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            **lib.stats(),
            "formats": {r[0]: r[1] for r in lib.conn.execute("SELECT format, COUNT(*) FROM files GROUP BY format ORDER BY 2 DESC")},
            "decades": {str(r[0]) + "s": r[1] for r in lib.conn.execute(
                "SELECT (year / 10) * 10 AS decade, COUNT(*) FROM albums WHERE year IS NOT NULL GROUP BY decade ORDER BY decade")},
            "review": count("SELECT COUNT(*) FROM files WHERE review IS NOT NULL"),
            "partial": count("SELECT COUNT(*) FROM albums WHERE COALESCE(json_extract(extra, '$.partial'), 0) = 1"),
            "with_cover": count("SELECT COUNT(*) FROM albums WHERE cover_file IS NOT NULL"),
            "collection": settings.collection_path,
            "sources": [{"name": src.alias, "path": src.folder, "mode": src.mode} for src in settings.import_.sources],
            "allow_changes": allow_changes, "busy": jobs.busy(), "waiting": jobs.waiting(), "login_required": bool(password),
            "watch": {"enabled": settings.watch.enabled, "every": settings.watch.interval, "last": watcher.last_job,
                      "sources": len(settings.import_.sources)},
        }

    @app.get("/api/settings", dependencies=guarded, summary="Every option with its kind, default and help (no secrets)")
    def settings_list():
        path = user_config_path()
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return {"path": str(path), "allow_changes": allow_changes, "sections": settings_api.describe(settings, text)}

    @app.post("/api/settings", dependencies=guarded,
              summary="**Change (unless a preview):** set options in your configuration file, checked as a whole before it is written")
    def settings_change(body: SettingsChange, request: Request):
        if not body.preview:
            can_change(request)
            if jobs.busy():
                raise HTTPException(409, "a task is running: wait for it to finish, or cancel it")
        path = user_config_path()
        if not path.exists():
            raise HTTPException(409, f"there is no configuration file yet ({path}): run `bdnex config --update`")
        try:
            return settings_api.change(settings, path, body.changes, apply=not body.preview)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    @app.get("/api/config", dependencies=guarded, summary="The settings that matter to the page (no secrets)")
    def config():
        return {"collection_path": settings.collection_path, "library": str(settings.library_path),
                "cover_dir": str(settings.cover_path), "paths": {"series": settings.paths.series, "oneshot": settings.paths.oneshot,
                                                                 "rules": [r.model_dump() for r in settings.paths.rules]},
                "sources": [src.model_dump() for src in settings.import_.sources],
                "write": settings.write.model_dump(), "api": {"host": settings.api.host, "port": settings.api.port,
                                                              "token_required": bool(token), "login_required": bool(password),
                                                              "allow_changes": allow_changes}}

    @app.get("/api/files/{file_id}/cover", dependencies=guarded, summary="The first page of a file, as a small picture")
    def file_cover(file_id: int, lib: Library = Depends(library)):
        from bdnex.importer import covers
        from bdnex.ui import thumbnail

        row = lib.file_by_id(file_id)
        if row is None:
            raise HTTPException(404, f"no file {file_id}")
        image = covers.front_cover(row["path"], row["format"]) if Path(row["path"]).exists() else None
        small = thumbnail.small_png(image, width=320) if image else None
        if not small:
            raise HTTPException(404, "no first page could be read from this file")
        return Response(small[0], media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/review", dependencies=guarded, summary="Matches that wait for a look (a different cover, or what the album page says)")
    def review(lib: Library = Depends(library)):
        items = []
        for row in lib.query(["review:true"], files=True):
            album = lib.get_album(row["id"]) if row["id"] is not None else None
            kept = album.get("cover_file") if album else None
            items.append({"file_id": row["file_id"], "path": row["path"], "format": row["format"], "reason": row["review"],
                          "cover_score": row.get("cover_score"),
                          "album": {"id": row["id"], "title": row["title"], "series": row["series"], "volume": row["volume"],
                                    "has_cover": bool(kept and (settings.cover_path / kept).exists())}})
        return {"count": len(items), "items": items}

    @app.get("/api/review/batch", dependencies=guarded,
             summary="The next files to check, as `bdnex review --batch` shows them (doubtful, and with `unmatched` / `all` more)")
    def review_batch_view(q: str = "", size: int = Query(50, ge=1, le=MAX_LIMIT), unmatched: bool = False,
                          all: bool = False, again: bool = False, lib: Library = Depends(library)):
        from bdnex.library import review_batch

        try:
            rows = review_batch.next_batch(lib, settings, size, terms(q), unmatched=unmatched, everything=all, again=again)
        except QueryError as err:
            raise HTTPException(400, f"bad query: {err}") from err
        items = [{"file_id": r.file_id, "path": r.path, "folder": r.folder, "kind": r.kind, "album": r.album,
                  "album_id": r.album_id, "album_url": r.album_url, "score": r.score, "cover": r.cover, "why": r.why,
                  "facts": r.facts, "previous": r.previous} for r in rows]
        return {"count": len(items), "total": rows.total, "items": items}

    @app.post("/api/review/{file_id}/decide", dependencies=changing,
              summary="**Change:** decide a file of a batch (`keep`, `unlink` or `leave`), flagged or not; it is marked checked")
    def decide_review(file_id: int, answer: ReviewAnswer, request: Request):
        from bdnex.library import review_batch

        if answer.action not in ("keep", "unlink", "leave"):
            raise HTTPException(400, "`action` is `keep`, `unlink` or `leave`")
        with Library.for_settings(settings) as lib:
            file = lib.file_by_id(file_id)
            if file is None:
                raise HTTPException(404, f"no file {file_id}")
            row = review_batch.Row(file["path"], "", None, None, None, None, file.get("cover_score"), "ok", [], file_id)
            review_batch.decide(lib, row, answer.action)
            request.state.detail = {"file": file["path"], "answer": answer.action}
            return {"ok": True, "status": row.status}

    @app.post("/api/review/{file_id}", dependencies=changing,
              summary="Answer a review: `keep`, `unlink`, `rematch` (a task that asks which album) or `album` (use that album)")
    def answer_review(file_id: int, answer: ReviewAnswer, request: Request):
        if answer.action not in ("keep", "unlink", "rematch", "album"):
            raise HTTPException(400, "`action` is `keep`, `unlink`, `rematch` or `album`")
        with Library.for_settings(settings) as lib:
            row = lib.file_by_id(file_id)
            if row is None or not row.get("review"):
                raise HTTPException(404, f"file {file_id} is not waiting for a review")
            if answer.action in ("keep", "unlink"):
                from bdnex.ui.review_cmd import settle

                settle(lib, row, answer.action)  # the same answers, the same effects as `bdnex review`
                request.state.detail = {"file": row["path"], "answer": answer.action}
                return {"ok": True}
        # rematch / album: the matching is a task (it reads a page, and may ask), started like any other
        options = {"preview": False, "paths": [row["path"]], "interactive": answer.interactive, "album": answer.album}
        try:
            job = jobs.start("rematch" if answer.action == "rematch" else "apply-album", options, allow_changes=allow_changes)
        except JobError as err:
            raise HTTPException(400, str(err)) from err
        except Busy as err:
            raise HTTPException(409, str(err)) from err
        request.state.detail = {"file": row["path"], "answer": answer.action, "album": answer.album, "job": job.id}
        return {"ok": True, "job": job.public()}

    @app.get("/api/duplicates", dependencies=guarded, summary="The same book in several files")
    def duplicates_view(lib: Library = Depends(library)):
        from bdnex.library.health import duplicates

        groups = duplicates(lib)
        return {"count": len(groups), "items": [
            {"why": g["why"], "files": [{"file_id": f["id"], "path": f["path"], "format": f["format"], "size": f["size"],
                                         "pages": f["page_count"], "spare": bool(f["spare"])} for f in g["files"]]}
            for g in groups]}

    @app.get("/api/check", dependencies=guarded, summary="Is the library consistent with itself and with the disk?")
    def check_view(matches: bool = Query(False, description="also weigh every match against its album page (`bdnex check --matches`)"),
                   lib: Library = Depends(library)):
        from bdnex.library.health import check

        found = check(lib, settings, matches=matches)
        def shown(f):
            entry = {"key": f.key, "problem": f.problem, "items": f.items, "advice": f.advice, "fixable": f.fixable}
            if f.key == "matches":  # its lines come in threes (path, album, why): give them as records too
                entry["matches"] = [{"path": f.items[i], "album": f.items[i + 1].strip(), "reason": f.items[i + 2].strip()}
                                    for i in range(0, len(f.items) - 2, 3)]
            return entry
        return {"count": len(found), "items": [shown(f) for f in found]}

    @app.get("/api/candidates", dependencies=guarded,
             summary="The albums each file could be, with where each score comes from (`bdnex candidates`)")
    def candidates(q: str = Query("", description="`bdnex ls --files` terms (files without album are included)"),
                   low: bool = Query(False, description="only the files whose match is in doubt"),
                   per_file: int = Query(5, ge=1, le=10, description="candidates per file"),
                   limit: int = Query(10, ge=1, le=50, description="files in this window"), offset: int = Query(0, ge=0),
                   cover: bool = Query(False, description="also compare covers (a request for each candidate whose cover is "
                                                          "not saved: keep `limit` small)"),
                   lib: Library = Depends(library)):
        from bdnex.importer import candidates, covers

        if cover and limit * per_file > 25:  # a GET that sends requests to the site: a few at a time
            raise HTTPException(400, "with `cover`, `limit` x `per_file` is at most 25 (each candidate whose cover is not "
                                     "saved is a request to the site)")
        try:
            items = candidates.build_report(settings, lib, terms(q), low=low, limit=per_file,
                                                cover=cover and covers.opencv_installed(), first=offset, files=limit)
        except candidates.NotReady as err:
            raise HTTPException(409, str(err)) from err
        except QueryError as err:
            raise HTTPException(400, f"bad query: {err}") from err
        return {"offset": offset, "limit": limit, "cover_compared": cover and covers.opencv_installed(), "items": items}

    @app.post("/api/marks", dependencies=guarded,
              summary="**Change (unless a preview):** put or remove your own marks on the files (or albums) a query finds, as `bdnex mark`")
    def marks(body: MarksChange, request: Request):
        from bdnex.ui import mark_cmd

        if not body.q.strip():
            raise HTTPException(400, "give a query: this changes every file it finds")
        if not body.set and not body.remove:
            raise HTTPException(400, "nothing to do: give `set` and/or `remove`")
        if not body.preview:
            can_change(request)
            if jobs.busy():
                raise HTTPException(409, "a task is running: wait for it to finish, or cancel it")
        puts = [(name.strip().lower(), str(value)) for name, value in body.set.items()]
        removes = [name.strip().lower() for name in body.remove]
        kind = "album" if body.albums else "file"
        with Library.for_settings(settings, readonly=body.preview) as lib:
            try:
                rows = lib.query(terms(body.q), files=not body.albums)
                ids = [r["id"] if body.albums else r["file_id"] for r in rows if (r["id"] if body.albums else r["file_id"])]
                if not body.preview:
                    mark_cmd.apply_marks(lib, kind, ids, puts, removes)
            except (QueryError, ValueError) as err:
                raise HTTPException(400, str(err)) from err
        request.state.detail = {"q": body.q, "kind": kind, "set": dict(puts), "remove": removes, "matched": len(ids)}
        return {"matched": len(ids), "changed": 0 if body.preview else len(ids), "kind": kind}

    # ------------------------------------------------------------------------- jobs
    @app.get("/api/jobs/available", dependencies=guarded, summary="The jobs that can be started")
    def available():
        return {"items": [{"command": name, "label": spec.label, "writes": spec.writes, "fields": list(spec.fields),
                           "preview": spec.preview, "asks": spec.asks} for name, spec in SPECS.items() if spec.listed],
                "allow_changes": allow_changes}

    @app.get("/api/jobs", dependencies=guarded, summary="The jobs of this server run, newest first")
    def job_list():
        return {"items": [{**job.public(after=10 ** 9), "lines": []} for job in jobs.all()]}

    @app.get("/api/jobs/{job_id}", dependencies=guarded, summary="One job, with its output from line `after`")
    def job_get(job_id: int, after: int = Query(0, ge=0)):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")
        return job.public(after=after)

    @app.post("/api/jobs", dependencies=guarded, summary="Start a job (a preview changes nothing)")
    def job_start(request: Request, body: JobRequest):
        if not body.preview:  # the real run is a change: all the safeguards apply
            can_change(request)
        try:
            options = {"preview": body.preview, "query": body.query, "paths": body.paths, "copy": body.copy_files,
                       "batch": body.batch,
                       "interactive": body.interactive, "steps": body.steps, "auto": body.auto, "files": body.files, "force": body.force,
                       "formats": body.formats, "older_than": body.older_than, "id": body.id, "place": body.place, "old": body.old, "new": body.new,
                       "album": body.album, "only": body.only, "fields": body.fields, "with_review": body.with_review}
            job = jobs.start(body.command, options, allow_changes=allow_changes)
            request.state.detail = {"command": body.command, "arguments": job.argv, "preview": body.preview}
        except JobError as err:
            raise HTTPException(400, str(err)) from err
        except PermissionError as err:
            raise HTTPException(403, str(err)) from err
        except Busy as err:
            raise HTTPException(409, str(err)) from err
        return job.public()

    @app.get("/api/jobs/{job_id}/events", dependencies=guarded,
             summary="A task's output as server-sent events (`text/event-stream`): one `line` event per output line, then `end`")
    async def job_events(job_id: int, after: int = Query(0, ge=0)):
        import asyncio
        import json

        from fastapi.responses import StreamingResponse

        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")

        async def stream():
            position = after
            while True:
                state = job.public(position)
                for line in state["lines"]:
                    yield f"event: line\ndata: {json.dumps(line)}\n\n"
                position = state["next"]
                if state["question"]:
                    yield f"event: question\ndata: {json.dumps(state['question'])}\n\n"
                if state["status"] != "running":
                    yield f"event: end\ndata: {json.dumps({'status': state['status'], 'exit_code': state['exit_code'], 'next': position})}\n\n"
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/jobs/{job_id}/image", dependencies=guarded, summary="The picture shown with the question a task is waiting on")
    def job_image(job_id: int):
        job = jobs.get(job_id)
        image = job.question_image if job else None
        if not image:
            raise HTTPException(404, "no picture with this question")
        return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/jobs/{job_id}/answer", dependencies=changing, summary="**Change:** answer the question a job is waiting on")
    def job_answer(job_id: int, body: QuestionAnswer):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")
        try:
            taken = jobs.answer(job_id, body.question, {"choice": body.choice, "choices": body.choices, "text": body.text})
        except JobError as err:
            raise HTTPException(400, str(err)) from err
        if not taken:
            raise HTTPException(409, "this question is not being asked (any more)")
        return {"answered": True}

    @app.post("/api/jobs/{job_id}/cancel", dependencies=changing, summary="Ask a running job to stop")
    def job_cancel(job_id: int):
        if not jobs.cancel(job_id):
            raise HTTPException(404, f"job {job_id} is not running")
        return {"ok": True}

    return app
