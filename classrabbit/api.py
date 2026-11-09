"""Local HTTP API. Browser UI and API share one origin and one watchlist."""

from contextlib import asynccontextmanager
import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .service import MissingWatch, WatchLimit, WatchService
from .ucla import Snapshot, SourceError


class AddWatch(BaseModel):
    source_url: str = Field(min_length=1, max_length=2048)


class WatchItem(BaseModel):
    id: str
    term: str
    class_id: str
    label: str
    snapshot: Snapshot | None
    error: str | None
    stale: bool
    failures: int
    added_at: str
    last_attempt_at: str | None
    next_check_at: str


class RefreshResult(BaseModel):
    watch: WatchItem
    cached: bool
    reopened: bool


class Section(BaseModel):
    class_id: str
    section: str
    url: str


class SourceInfo(BaseModel):
    id: str
    label: str
    mode: str
    realtime: bool
    refresh_interval_seconds: int | None
    check_interval_seconds: int
    notice: str
    session_status: str | None = None


class SearchResult(BaseModel):
    sections: list[Section]
    source: SourceInfo
    cached: bool


class ReopeningEvent(BaseModel):
    id: int
    term: str
    class_id: str
    observed_at: str
    snapshot: Snapshot
    kind: str


def create_app(database=None, source=None, *, worker=True):
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = WatchService(database or os.environ.get("CLASSRABBIT_DB", ".local/app.sqlite3"), source)
        if worker:
            app.state.service.start()
        try:
            yield
        finally:
            app.state.service.stop()

    app = FastAPI(title="Classrabbit API", version="0.2.0", lifespan=lifespan,
                  description="Single-user local enrollment tracker. Complete section sets are checked every 30 seconds by default; underlying UCLA public-source freshness remains unverified.")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_browser_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
            if origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse({"detail": "Cross-origin changes are not accepted."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/":
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(MissingWatch)
    async def missing_watch(request, exc):
        return JSONResponse({"detail": "Watched section not found."}, status_code=404)

    @app.exception_handler(WatchLimit)
    async def watch_limit(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/health", tags=["Status"])
    def health(request: Request):
        worker_thread = request.app.state.service.worker
        return {"status": "ok", "version": "0.2.0", "scheduler_running": bool(worker_thread and worker_thread.is_alive())}

    @app.get("/api/source", response_model=SourceInfo, tags=["Status"])
    def source_info(request: Request):
        return request.app.state.service.source_info()

    @app.get("/api/sections", response_model=SearchResult, tags=["Courses"], responses={502: {"description": "Upstream source unavailable or unrecognized"}})
    def sections(request: Request,
                 term: Annotated[str, Query(pattern=r"^\d{2}[FWS12]$")],
                 subject: Annotated[str, Query(min_length=2, max_length=20, pattern=r"^[A-Za-z &-]+$")],
                 course: Annotated[str, Query(pattern=r"^\d{1,4}[A-Za-z]{0,2}$")]):
        try:
            return request.app.state.service.search(term, subject, course)
        except SourceError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/watchlist", response_model=list[WatchItem], tags=["Watchlist"])
    def watchlist(request: Request):
        return request.app.state.service.list_watches()

    @app.post("/api/watchlist", response_model=WatchItem, status_code=201, tags=["Watchlist"],
              responses={200: {"description": "Section already watched"}, 400: {"description": "Invalid public class link"}})
    def add_watch(body: AddWatch, request: Request, response: Response):
        try:
            item, created = request.app.state.service.add(body.source_url)
            response.status_code = 201 if created else 200
            return item
        except SourceError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/watchlist/{watch_id}/refresh", response_model=RefreshResult, tags=["Watchlist"])
    def refresh(watch_id: str, request: Request):
        """Reuse an observation until its next scheduled check. Failed checks retain the last good observation."""
        return request.app.state.service.refresh(watch_id)

    @app.delete("/api/watchlist/{watch_id}", status_code=204, tags=["Watchlist"])
    def remove(watch_id: str, request: Request):
        request.app.state.service.remove(watch_id)
        return Response(status_code=204)

    @app.get("/api/events", response_model=list[ReopeningEvent], tags=["Activity"])
    def events(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return request.app.state.service.events(limit)

    @app.get("/api/events/stream", tags=["Activity"], response_class=StreamingResponse)
    async def event_stream(request: Request, after: Annotated[int, Query(ge=0)] = 0):
        """Push detected reopening events to an open browser; this does not change source freshness."""
        service = request.app.state.service
        previous = request.headers.get("last-event-id", "0")
        cursor = max(after, int(previous)) if previous.isdigit() and len(previous) < 19 else after

        async def messages():
            nonlocal cursor
            queue = service.event_hub.subscribe()
            try:
                # Subscribe before replay so events arriving during the read cannot be lost.
                yield "event: connected\ndata: {}\n\n"
                pending = await asyncio.to_thread(service.events, 100)
                for event in reversed(pending):
                    if event["id"] > cursor and event["kind"] == "combination_opening":
                        cursor = event["id"]
                        yield f"id: {cursor}\nevent: reopening\ndata: {json.dumps(event)}\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=10)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event["id"] > cursor and event["kind"] == "combination_opening":
                        cursor = event["id"]
                        yield f"id: {cursor}\nevent: reopening\ndata: {json.dumps(event)}\n\n"
            finally:
                service.event_hub.unsubscribe(queue)

        return StreamingResponse(messages(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return app
