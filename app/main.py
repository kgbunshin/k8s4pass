"""CKAD Lab API and front end.

Run (from ckad-lab/):
    .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

There is no authentication: that is why it listens on 127.0.0.1 only by default. Before
exposing it to the internet you need to add login, rate limiting and strong per-session isolation.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import sessions, terminal
from .sessions import CapacityError, StateError, manager

STATIC = Path(__file__).resolve().parent / "static"
EXTRA_ORIGINS = {o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o}


@asynccontextmanager
async def lifespan(_: FastAPI):
    sessions.preflight()
    manager.cleanup_orphans()
    manager.start_reaper()
    yield
    manager.shutdown()  # do not leave clusters running when the server stops


app = FastAPI(title="CKAD Lab", lifespan=lifespan)


@app.middleware("http")
async def reject_cross_origin_writes(request: Request, call_next):
    if request.method in ("POST", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host") and origin not in EXTRA_ORIGINS:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
    return await call_next(request)


class NewSession(BaseModel):
    domain: str | None = None
    count: int = Field(3, ge=1, le=20)
    seed: int | None = None


class Regrade(BaseModel):
    question: str | None = None  # question id; empty = all


def _get(sid: str) -> sessions.Session:
    try:
        return manager.get(sid)
    except KeyError:
        raise HTTPException(404, "session not found") from None


@app.get("/api/catalog")
def catalog() -> dict:
    return {"domains": manager.catalog(), "max_sessions": sessions.MAX_SESSIONS,
            "session_minutes": sessions.SESSION_MINUTES, "review_minutes": sessions.REVIEW_MINUTES}


@app.post("/api/sessions", status_code=201)
def create_session(body: NewSession) -> dict:
    try:
        s = manager.create(body.domain, body.count, body.seed)
    except CapacityError as e:
        raise HTTPException(429, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return manager.view(s)


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict:
    return manager.view(_get(sid))


@app.post("/api/sessions/{sid}/finish")
def finish_session(sid: str) -> dict:
    _get(sid)
    try:
        return manager.view(manager.finish(sid))
    except StateError as e:
        raise HTTPException(409, str(e)) from None


@app.post("/api/sessions/{sid}/regrade")
def regrade_session(sid: str, body: Regrade | None = None) -> dict:
    _get(sid)
    try:
        return manager.view(manager.regrade(sid, body.question if body else None))
    except StateError as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.delete("/api/sessions/{sid}", status_code=204)
def delete_session(sid: str) -> None:
    _get(sid)
    manager.destroy(sid)


@app.websocket("/ws/terminal/{sid}")
async def terminal_ws(ws: WebSocket, sid: str) -> None:
    # Browsers always send Origin; without this check, any site open in the student's
    # browser could open a shell in this session (cross-site WebSocket hijacking).
    origin = ws.headers.get("origin")
    if not origin or (urlparse(origin).netloc != ws.headers.get("host") and origin not in EXTRA_ORIGINS):
        await ws.close(code=4403)
        return
    try:
        s = manager.get(sid)
    except KeyError:
        await ws.close(code=4404)
        return
    if not s.terminal_open:  # includes the review window after grading
        await ws.close(code=4409)
        return
    await ws.accept()
    try:
        cols = int(ws.query_params.get("cols", 80))
        rows = int(ws.query_params.get("rows", 24))
    except ValueError:
        cols, rows = 80, 24
    try:
        await terminal.bridge(ws, s.shell, cols, rows)
    finally:
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):  # already closed by the client
            pass


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
