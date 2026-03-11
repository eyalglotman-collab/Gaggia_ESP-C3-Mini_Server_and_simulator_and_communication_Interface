"""FastAPI entry point for the transport-first simulator template."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from server.api.routes import router

APP_ROOT = Path(__file__).resolve().parent
UI_ROOT = APP_ROOT / "ui"
PROJECT_ROOT = APP_ROOT.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
FIRMWARE_VERSION_FILE = PROJECT_ROOT / "firmware" / "esp32c3_bridge" / "VERSION"
APP_NAME = "Eyal Espresso Server Simulator"
APP_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip()
FIRMWARE_VERSION = FIRMWARE_VERSION_FILE.read_text(encoding="utf-8").strip()
APP_BUILD_TIMESTAMP = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

app = FastAPI(title=APP_NAME)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/app-info")
def app_info() -> dict[str, str]:
    """@brief Return simulator metadata for the startup splash screen.

    @details Exposes the application name, simulator version, firmware version,
    and backend build timestamp captured when the server module was loaded so
    the UI can present a startup identity panel without hard-coding release
    metadata.
    @return Metadata used by the simulator startup splash screen.
    """

    return {
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "firmware_version": FIRMWARE_VERSION,
        "build_timestamp": APP_BUILD_TIMESTAMP,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_ROOT / "index.html")
