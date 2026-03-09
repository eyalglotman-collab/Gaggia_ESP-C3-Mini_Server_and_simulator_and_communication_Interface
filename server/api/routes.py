"""API route registration helpers for the transport-first simulator."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.sim.link_state_machine import link_runtime

router = APIRouter(prefix="/api", tags=["simulator"])


class PortRequest(BaseModel):
    port_name: str = Field(..., min_length=1, max_length=64)


class TransportConfigRequest(BaseModel):
    serial_port: str = Field("COM4", min_length=1, max_length=64)
    wifi_ssid: str = Field("EyalSimulatorAP", min_length=1, max_length=64)
    wifi_password: str = Field("espresso1234", max_length=64)
    server_ip: str = Field("192.168.4.1", min_length=1, max_length=64)
    server_port: int = Field(3333, ge=1, le=65535)
    wifi_connect_timeout_ms: int = Field(10000, ge=1, le=60000)
    tcp_connect_timeout_ms: int = Field(3000, ge=1, le=60000)
    keepalive_period_ms: int = Field(100, ge=1, le=5000)


@router.get("/link")
def get_link_snapshot() -> dict[str, object]:
    return asdict(link_runtime.get_snapshot())


@router.post("/config")
def update_transport_config(request: TransportConfigRequest) -> dict[str, object]:
    return asdict(
        link_runtime.configure_transport(
            serial_port=request.serial_port,
            wifi_ssid=request.wifi_ssid,
            wifi_password=request.wifi_password,
            server_ip=request.server_ip,
            server_port=request.server_port,
            wifi_connect_timeout_ms=request.wifi_connect_timeout_ms,
            tcp_connect_timeout_ms=request.tcp_connect_timeout_ms,
            keepalive_period_ms=request.keepalive_period_ms,
        )
    )


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
