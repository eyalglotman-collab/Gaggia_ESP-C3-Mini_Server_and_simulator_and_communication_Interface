"""API route registration helpers for the transport-first simulator."""

from __future__ import annotations

import traceback
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


class SendDataRequest(BaseModel):
    payload_text: str = Field("espresso_payload", min_length=1, max_length=512)


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
        print(f"\n[api] UNHANDLED EXCEPTION in {action_name}: {exc}", flush=True)
        traceback.print_exc()
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


@router.post("/transport/release-com")
def release_com_port() -> dict[str, object]:
    """@brief Force-release the configured COM port from likely owning PIDs.

    @details The simulator first closes its own handle, then asks the
    transport layer to terminate external serial tools that still appear to
    hold the configured COM port.
    """

    return _run_snapshot_action("release_com_port", link_runtime.force_release_transport)


@router.post("/transport/toggle-wifi")
def toggle_wifi() -> dict[str, object]:
    """@brief Toggle low-level Wi-Fi availability for transport testing.

    @details This is a simulator-only operator control used to test reset and
    connect behavior when Wi-Fi is intentionally disabled without changing the
    saved SSID/password configuration values.
    """

    return _run_snapshot_action("toggle_wifi", link_runtime.toggle_wifi_enabled)


@router.post("/command/reset")
def command_reset() -> dict[str, object]:
    return _run_snapshot_action("command_reset", link_runtime.reset)


@router.post("/command/initialize")
def command_initialize() -> dict[str, object]:
    return _run_snapshot_action("command_initialize", link_runtime.initialize)


@router.post("/command/keepalive")
def command_keepalive() -> dict[str, object]:
    return _run_snapshot_action("command_keepalive", link_runtime.send_keepalive)


@router.post("/command/send-data")
def command_send_data(request: SendDataRequest | None = None) -> dict[str, object]:
    """@brief Trigger one server-side DATA transmit action.

    @details The low-level runtime exposes this route only after the ESP
    controller has reached keepalive-ready connection state.
    """

    payload_text = request.payload_text if request is not None else "espresso_payload"
    return _run_snapshot_action("command_send_data", lambda: link_runtime.send_data(payload_text))


@router.post("/telemetry/reset-total-errors")
def telemetry_reset_total_errors() -> dict[str, object]:
    """@brief Reset the aggregate telemetry error counter on operator request."""

    return _run_snapshot_action("telemetry_reset_total_errors", link_runtime.reset_total_errors)


@router.post("/telemetry/reset-max-delay")
def telemetry_reset_max_delay() -> dict[str, object]:
    """@brief Reset the transport max-delay telemetry value on operator request."""

    return _run_snapshot_action("telemetry_reset_max_delay", link_runtime.reset_transport_max_delay)
