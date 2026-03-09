"""FastAPI entry point for the transport-first simulator template."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from server.api.routes import router

APP_ROOT = Path(__file__).resolve().parent
UI_ROOT = APP_ROOT / "ui"

app = FastAPI(title="Eyal Espresso Server Simulator")
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_ROOT / "index.html")