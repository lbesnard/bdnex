"""The FastAPI application. It only reads: the database is opened read-only for every request."""

from __future__ import annotations

import hmac
import shlex
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from bdnex.config import Settings
from bdnex.library import Library
from bdnex.library.query import QueryError

STATIC = Path(__file__).resolve().parent / "static"
MAX_LIMIT = 1000


def create_app(settings: Settings, token: Optional[str] = None):
    """The application for ``settings``; ``token`` (default ``api.token``) is required when set."""
    try:
        from importlib.metadata import version
        __version__ = version("bdnex")
    except Exception:
        __version__ = "unknown"

    token = settings.api.token if token is None else token
    app = FastAPI(
        title="bdnex", version=__version__,
        description="Read-only view of your bdnex library. The `q` parameter takes the same terms as "
                    "`bdnex ls` (`series:nains year:2010..2015 ^genre:humour`).")

    def allowed(request: Request) -> None:
        if not token:
            return
        given = request.headers.get("authorization", "")
        supplied = given[7:] if given.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "a valid `Authorization: Bearer <token>` header is required",
                                headers={"WWW-Authenticate": "Bearer"})

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

    guarded = [Depends(allowed)]

    def terms(q: str) -> list[str]:
        try:
            return shlex.split(q)
        except ValueError as err:
            raise HTTPException(400, f"bad query: {err}") from err

    def page(rows: list[dict[str, Any]], limit: int, offset: int) -> dict[str, Any]:
        return {"count": len(rows), "offset": offset, "limit": limit, "items": rows[offset:offset + limit]}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health", dependencies=guarded, summary="Is the server up, and which library does it read?")
    def health(lib: Library = Depends(library)):
        return {"status": "ok", "version": __version__, "schema": lib.schema_version, "token_required": bool(token)}

    @app.get("/api/stats", dependencies=guarded, summary="Counts of files, albums, series...")
    def stats(lib: Library = Depends(library)):
        return lib.stats()

    @app.get("/api/albums", dependencies=guarded, summary="Albums matching a query")
    def albums(q: str = Query("", description="`bdnex ls` terms"), limit: int = Query(100, ge=1, le=MAX_LIMIT),
               offset: int = Query(0, ge=0), lib: Library = Depends(library)):
        try:
            rows = lib.query(terms(q))
        except QueryError as err:
            raise HTTPException(400, f"bad query: {err}") from err
        return page(rows, limit, offset)

    @app.get("/api/albums/{album_id}", dependencies=guarded, summary="One album, with its files")
    def album(album_id: int, lib: Library = Depends(library)):
        found = lib.get_album(album_id)
        if found is None:
            raise HTTPException(404, f"no album {album_id}")
        found["files"] = lib.files_for_album(album_id)
        return found

    @app.get("/api/files", dependencies=guarded, summary="Files matching a query")
    def files(q: str = Query("", description="`bdnex ls --files` terms"),
              limit: int = Query(100, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0),
              lib: Library = Depends(library)):
        try:
            rows = lib.query(terms(q), files=True)
        except QueryError as err:
            raise HTTPException(400, f"bad query: {err}") from err
        return page(rows, limit, offset)

    @app.get("/api/series", dependencies=guarded, summary="Series, what you own and what is missing")
    def series(name: str = Query("", description="words of the series name"), missing: bool = False,
               lib: Library = Depends(library)):
        rows = lib.series_overview(name.split())
        if missing:
            rows = [r for r in rows if r["missing"]]
        return {"count": len(rows), "items": rows}

    @app.get("/api/trash", dependencies=guarded, summary="Files waiting in the trash")
    def trash(lib: Library = Depends(library)):
        rows = lib.trash_entries()
        return {"count": len(rows), "items": rows}

    return app
