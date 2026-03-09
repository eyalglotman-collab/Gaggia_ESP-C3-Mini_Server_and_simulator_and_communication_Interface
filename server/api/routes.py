"""API route registration helpers for the transport-first simulator."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.sim.link_state_machine import link_runtime

router = APIRouter(prefix="/api", tags=["simulator"])


class PortRequest(BaseModel):
    port_name: str = Field(..., min_length=1, max_length=64)


@router.get("/link")
def get_link_snapshot() -> dict[str, object]:
    return asdict(link_runtime.get_snapshot())


@router.post("/transport/open")
def open_transport(request: PortRequest) -> dict[str, object]:
    try:
        link_runtime.configure_port(request.port_name)
        return asdict(link_runtime.open_transport())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/transport/close")
def close_transport() -> dict[str, object]:
    return asdict(link_runtime.close_transport())


@router.post("/command/reset")
def command_reset() -> dict[str, object]:
    try:
        return asdict(link_runtime.reset())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command/initialize")
def command_initialize() -> dict[str, object]:
    try:
        return asdict(link_runtime.initialize())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command/connect")
def command_connect() -> dict[str, object]:
    try:
        return asdict(link_runtime.connect())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command/disconnect")
def command_disconnect() -> dict[str, object]:
    try:
        return asdict(link_runtime.disconnect())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command/keepalive")
def command_keepalive() -> dict[str, object]:
    try:
        return asdict(link_runtime.send_keepalive())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc