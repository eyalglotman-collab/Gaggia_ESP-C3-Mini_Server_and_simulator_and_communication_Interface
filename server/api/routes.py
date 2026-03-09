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


def _run_snapshot_action(action_name: str, action) -> dict[str, object]:
    """@brief Execute one simulator action and always return a JSON snapshot.

    @details Runtime errors remain client-action faults, while unexpected
    exceptions are latched into the low-level runtime as fail-safe errors so
    the UI stays responsive instead of surfacing an opaque server crash.
    """

    if action_name == "get_link_snapshot":
        link_runtime.note_monitor_event("poll", "UI snapshot refresh", throttle_snapshot=True)
    else:
        link_runtime.note_monitor_event("api", f"{action_name} invoked")

    try:
        return asdict(action())
    except RuntimeError as exc:
        link_runtime.note_monitor_event("runtime-error", f"{action_name} -> {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive fail-safe
        link_runtime.note_monitor_event("exception", f"{action_name} raised {type(exc).__name__}")
        return asdict(link_runtime.capture_internal_failure(action_name, exc))


@router.get("/link")
def get_link_snapshot() -> dict[str, object]:
    return _run_snapshot_action("get_link_snapshot", link_runtime.get_snapshot)


@router.post("/config")
def update_transport_config(request: TransportConfigRequest) -> dict[str, object]:
    return _run_snapshot_action(
        "update_transport_config",
        lambda: link_runtime.configure_transport(
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
    return _run_snapshot_action(
        "open_transport",
        lambda: (link_runtime.configure_port(request.port_name), link_runtime.open_transport())[1],
    )


@router.post("/transport/close")
def close_transport() -> dict[str, object]:
    return _run_snapshot_action("close_transport", link_runtime.close_transport)


@router.post("/command/reset")
def command_reset() -> dict[str, object]:
    return _run_snapshot_action("command_reset", link_runtime.reset)


@router.post("/command/initialize")
def command_initialize() -> dict[str, object]:
    return _run_snapshot_action("command_initialize", link_runtime.initialize)


@router.post("/command/connect")
def command_connect() -> dict[str, object]:
    return _run_snapshot_action("command_connect", link_runtime.connect)


@router.post("/command/disconnect")
def command_disconnect() -> dict[str, object]:
    return _run_snapshot_action("command_disconnect", link_runtime.disconnect)


@router.post("/command/keepalive")
def command_keepalive() -> dict[str, object]:
    return _run_snapshot_action("command_keepalive", link_runtime.send_keepalive)
