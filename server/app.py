"""FastAPI app for the personal Abao instance."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Iterator, Optional

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.abao import Abao, AbaoPaths


ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

load_dotenv(ROOT / ".env")


class ChatRequest(BaseModel):
    text: str


class HistoryMessage(BaseModel):
    role: str
    text: str
    created_at: str


class AbaoRuntime:
    def __init__(self) -> None:
        self._lock = Lock()
        self._abao = Abao(AbaoPaths(
            config_dir=ROOT / "config",
            data_dir=ROOT / "data",
        ))

    def stream(self, text: str) -> Iterator[str]:
        with self._lock:
            yield from self._abao.converse_stream(text, speaker="user")

    def history(self, limit: int) -> list[HistoryMessage]:
        with self._lock:
            records = self._abao.memory.store.recent_conversations(limit=limit)
        records.reverse()
        messages = []
        for r in records:
            if r.content.startswith("[user] "):
                messages.append(HistoryMessage(
                    role="user",
                    text=r.content[len("[user] "):],
                    created_at=r.created_at,
                ))
            elif r.content.startswith("[self] "):
                messages.append(HistoryMessage(
                    role="assistant",
                    text=r.content[len("[self] "):],
                    created_at=r.created_at,
                ))
        return messages

    def close(self) -> None:
        self._abao.shutdown()


runtime = AbaoRuntime()
app_config = {}
try:
    app_config = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text(encoding="utf-8")).get("app", {})
except Exception:
    app_config = {}
DISPLAY_NAME = app_config.get("display_name", "阿宝")
APP_SLUG = app_config.get("slug", "abao")

app = FastAPI(title=DISPLAY_NAME)


@app.on_event("shutdown")
def shutdown() -> None:
    runtime.close()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "name": APP_SLUG, "display_name": DISPLAY_NAME}


@app.get("/api/history")
def history(
    limit: int = 20,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _authorize(authorization)
    limit = max(2, min(80, limit))
    return {"messages": [m.model_dump() for m in runtime.history(limit)]}


@app.post("/api/chat/stream")
def chat_stream(
    req: ChatRequest,
    authorization: Optional[str] = Header(default=None),
) -> StreamingResponse:
    _authorize(authorization)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    def events() -> Iterator[str]:
        for chunk in runtime.stream(text):
            yield _sse({"type": "delta", "text": chunk})
        yield _sse({"type": "done"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _authorize(authorization: Optional[str]) -> None:
    token = os.environ.get("ABAO_OWNER_TOKEN", "").strip()
    if not token:
        return
    expected = f"Bearer {token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/manifest.webmanifest")
def manifest() -> JSONResponse:
    return JSONResponse({
        "name": DISPLAY_NAME,
        "short_name": DISPLAY_NAME,
        "description": "一个私人数字生命实例的移动入口",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#fff8ee",
        "theme_color": "#fff8ee",
        "icons": [{
            "src": "/icon.svg",
            "sizes": "any",
            "type": "image/svg+xml",
            "purpose": "any maskable",
        }],
    })


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/{path:path}")
def static_file(path: str) -> FileResponse:
    target = WEB_DIR / path
    if target.is_file():
        return FileResponse(target)
    return FileResponse(WEB_DIR / "index.html")
